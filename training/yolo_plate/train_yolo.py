"""
train_yolo.py — เทรน YOLO สำหรับ "หาตำแหน่งป้ายทะเบียน" ในรูป

dataset มาจาก Roboflow (export แบบ YOLOv11) ซึ่งจะได้โฟลเดอร์หน้าตาแบบนี้:
    thai_plate_detection.vXi.yolov11/
        data.yaml           <- บอกว่ารูป train/valid/test อยู่ไหน และมีกี่ class
        train/images, train/labels
        valid/images, valid/labels
        test/images,  test/labels

ขั้นตอนที่สคริปต์นี้ทำ:
    1) เช็คว่า data.yaml และโฟลเดอร์รูปมีอยู่จริง (กัน train ไปแล้วค่อยมา error)
    2) เลือกอุปกรณ์อัตโนมัติ: มีการ์ดจอ (CUDA) ใช้ GPU, ไม่มีใช้ CPU
    3) เทรนโดยเริ่มจากโมเดล pretrained (yolo11n.pt) — เรียกว่า transfer learning
       โมเดลรู้จักรูปร่างวัตถุทั่วไปมาแล้ว เลยเรียนรู้ป้ายทะเบียนได้เร็วกว่าเริ่มจากศูนย์มาก
    4) วัดผลกับชุด test (รูปที่โมเดลไม่เคยเห็นตอนเทรน) แล้วสรุป mAP / precision / recall
    5) (ถ้าใส่ --deploy) copy best.pt ไปแทนที่ models/yolo/best.pt ของระบบจริง โดยสำรองไฟล์เก่าไว้ก่อน

วิธีใช้ (รันจากโฟลเดอร์ไหนก็ได้):
    python training/yolo_plate/train_yolo.py
    python training/yolo_plate/train_yolo.py --data "D:\\roboflow\\thai_plate_detection.v4i.yolov11\\data.yaml"
    python training/yolo_plate/train_yolo.py --epochs 150 --batch 8 --device cpu
    python training/yolo_plate/train_yolo.py --resume training/outputs/yolo/<ชื่อรอบ>/weights/last.pt
    python training/yolo_plate/train_yolo.py --deploy      # เทรนเสร็จแล้วเอาไปใช้ในระบบเลย

ผลลัพธ์อยู่ที่ training/outputs/yolo/<ชื่อรอบ>/
    weights/best.pt   <- โมเดลที่ดีที่สุด (วัดจาก valid set) ตัวนี้แหละที่เอาไปใช้
    weights/last.pt   <- โมเดลรอบสุดท้าย (ใช้ --resume เทรนต่อได้ถ้าเทรนค้างกลางทาง)
    results.png, confusion_matrix.png, ... <- กราฟผลการเทรน
"""

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # ให้ import train_config.py (อยู่ในโฟลเดอร์ training) ได้
import train_config as cfg


def parse_args():
    # ค่า default ตั้งตามที่เคยเทรนได้ผลดี (runs/detect/thai-plate: yolo11n, 100 epochs, imgsz 640, batch 16)
    p = argparse.ArgumentParser(description="เทรน YOLO หาป้ายทะเบียน (dataset จาก Roboflow)")
    p.add_argument("--data", default=str(cfg.YOLO_DATA_YAML), help="path ไปที่ data.yaml ของ Roboflow")
    p.add_argument("--model", default=None,
                   help="โมเดลตั้งต้น (default: models/detector/yolo11n.pt ถ้ามี ไม่งั้นโหลด yolo11n.pt)")
    p.add_argument("--epochs", type=int, default=100, help="จำนวนรอบที่ให้ดู dataset ทั้งหมด")
    p.add_argument("--imgsz", type=int, default=640, help="ขนาดรูปที่ย่อ/ขยายก่อนเข้าโมเดล")
    p.add_argument("--batch", type=int, default=16, help="จำนวนรูปต่อ 1 step (การ์ดจอ RAM น้อยให้ลดเหลือ 8 หรือ 4)")
    p.add_argument("--device", default=None, help="'0' = GPU ตัวแรก, 'cpu' = CPU (default: เลือกให้อัตโนมัติ)")
    p.add_argument("--patience", type=int, default=20,
                   help="ถ้าผล valid ไม่ดีขึ้นติดกันกี่ epoch ให้หยุดก่อน (กัน overfit + ประหยัดเวลา)")
    p.add_argument("--workers", type=int, default=4, help="จำนวน process โหลดรูป (Windows ถ้า error ให้ใส่ 0)")
    p.add_argument("--name", default=None, help="ชื่อรอบการเทรน (default: plate_ปีเดือนวัน_เวลา)")
    p.add_argument("--resume", default=None, help="path ไปที่ last.pt เพื่อเทรนต่อจากที่ค้างไว้")
    p.add_argument("--deploy", action="store_true",
                   help="เทรนเสร็จแล้ว copy best.pt ไปแทน models/yolo/best.pt (สำรองไฟล์เก่าไว้ให้)")
    return p.parse_args()


def pick_device(device_arg):
    """ถ้าไม่ได้ระบุ device มา: มี GPU ใช้ GPU, ไม่มีใช้ CPU"""
    if device_arg:
        return device_arg
    import torch
    if torch.cuda.is_available():
        print(f"✅ เจอ GPU: {torch.cuda.get_device_name(0)} -> ใช้ GPU เทรน")
        return "0"
    print("⚠️  ไม่เจอ GPU -> เทรนด้วย CPU (ช้ากว่ามาก แนะนำลด --epochs หรือ --batch)")
    return "cpu"


def check_dataset(data_yaml: Path) -> dict:
    """
    เช็คก่อนเทรนว่า data.yaml กับโฟลเดอร์รูปมีจริง
    ทำไมต้องเช็ค: Roboflow บางเวอร์ชันเขียน path ใน data.yaml เป็น ../train/images
    ซึ่ง ultralytics จะหาไม่เจอ แล้วไป error ตอนเริ่มเทรน (หรือแย่กว่านั้นคือไปโหลด dataset ตัวอื่น)
    """
    import yaml

    if not data_yaml.exists():
        sys.exit(
            f"❌ ไม่เจอ data.yaml: {data_yaml}\n"
            "   - export dataset จาก Roboflow แบบ YOLOv11 แล้วแตกไฟล์\n"
            "   - ส่ง path เข้ามาด้วย --data หรือแก้ YOLO_DATA_YAML ใน training/train_config.py"
        )

    with open(data_yaml, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    # ultralytics อ่าน path แบบ relative โดยเทียบกับ key "path" (ถ้ามี) หรือโฟลเดอร์ที่ data.yaml อยู่
    base = Path(data.get("path") or data_yaml.parent)
    if not base.is_absolute():
        base = data_yaml.parent / base

    problems = []
    for split in ("train", "val"):
        if split not in data:
            problems.append(f"data.yaml ไม่มี key '{split}'")
            continue
        p = Path(data[split])
        p = p if p.is_absolute() else (base / p)
        if not p.exists():
            problems.append(f"{split}: ไม่เจอโฟลเดอร์ {p.resolve()}")
    if problems:
        sys.exit(
            "❌ dataset มีปัญหา:\n   - " + "\n   - ".join(problems) +
            "\n   ถ้า data.yaml เขียนเป็น ../train/images ให้แก้เป็น train/images (และ valid/images, test/images)"
        )

    print(f"✅ dataset: {data_yaml}")
    print(f"   จำนวน class: {data.get('nc')}  ชื่อ class: {data.get('names')}")
    return data


def deploy(best_pt: Path):
    """copy best.pt ไปใช้ในระบบจริง โดยสำรองตัวเก่าไว้ (ถ้าโมเดลใหม่แย่กว่า จะได้ย้อนกลับได้)"""
    target = cfg.PLATE_YOLO_MODEL
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        backup = target.with_name(f"best_backup_{datetime.now():%Y%m%d_%H%M%S}.pt")
        shutil.copy2(target, backup)
        print(f"💾 สำรองโมเดลเก่าไว้ที่: {backup}")
    shutil.copy2(best_pt, target)
    print(f"🚀 อัปเดตโมเดลที่ระบบใช้แล้ว: {target}")
    print("   (ต้อง restart worker / docker ให้โหลดโมเดลใหม่)")


def main():
    args = parse_args()

    # import ตรงนี้ (ไม่ใช่บนสุด) เพื่อให้ --help ทำงานได้แม้ยังไม่ได้ติดตั้ง ultralytics
    from ultralytics import YOLO

    device = pick_device(args.device)

    # ---------- เทรนต่อจากที่ค้างไว้ ----------
    # last.pt จำค่าทุกอย่างของรอบเดิมไว้แล้ว (epochs, batch, data, ...) เลยสั่ง resume=True อย่างเดียวพอ
    if args.resume:
        print(f"\n▶️  เทรนต่อจาก: {args.resume}")
        model = YOLO(args.resume)
        model.train(resume=True)
        save_dir = Path(model.trainer.save_dir)
        data_yaml = Path(model.trainer.args.data).resolve()   # ใช้ dataset เดียวกับรอบเดิม
        data = check_dataset(data_yaml)
    else:
        data_yaml = Path(args.data).resolve()
        data = check_dataset(data_yaml)

        # ---------- เลือกโมเดลตั้งต้น ----------
        if args.model:
            base_model = args.model
        elif cfg.CAR_YOLO_MODEL.exists():
            base_model = str(cfg.CAR_YOLO_MODEL)   # yolo11n.pt ที่มีอยู่แล้วในโปรเจกต์ ไม่ต้องโหลดใหม่
        else:
            base_model = "yolo11n.pt"              # ultralytics จะโหลดให้อัตโนมัติ

        run_name = args.name or f"plate_{datetime.now():%Y%m%d_%H%M}"

        print("\n" + "=" * 60)
        print(" เริ่มเทรน YOLO หาป้ายทะเบียน")
        print("=" * 60)
        print(f"โมเดลตั้งต้น : {base_model}")
        print(f"epochs       : {args.epochs} (หยุดก่อนถ้าไม่ดีขึ้น {args.patience} epoch ติด)")
        print(f"imgsz/batch  : {args.imgsz} / {args.batch}")
        print(f"device       : {device}")
        print(f"ผลลัพธ์       : {cfg.YOLO_OUTPUT_DIR / run_name}")
        print("=" * 60 + "\n")

        model = YOLO(base_model)
        model.train(
            data=str(data_yaml),
            epochs=args.epochs,
            imgsz=args.imgsz,
            batch=args.batch,
            device=device,
            patience=args.patience,
            workers=args.workers,
            project=str(cfg.YOLO_OUTPUT_DIR),   # เก็บผลไว้ใน training/outputs/yolo แทน runs/detect
            name=run_name,
            exist_ok=False,                     # ชื่อซ้ำจะได้ชื่อใหม่ต่อท้ายเลข ไม่เขียนทับรอบเก่า
            seed=0,                             # สุ่มแบบเดิมทุกครั้ง ผลจะได้เทียบกันได้
            plots=True,
        )
        save_dir = Path(model.trainer.save_dir)

    best_pt = save_dir / "weights" / "best.pt"
    if not best_pt.exists():
        sys.exit(f"❌ ไม่เจอ {best_pt} — การเทรนอาจไม่สำเร็จ ดู log ด้านบน")

    # ---------- วัดผลกับชุด test ----------
    # valid set ถูกใช้เลือก best.pt ไปแล้ว ผลเลยดูดีเกินจริงนิดหน่อย
    # test set คือรูปที่โมเดลไม่เคยเห็นเลย ตัวเลขนี้คือความแม่นยำที่น่าเชื่อถือที่สุด
    split = "test" if "test" in data else "val"
    print(f"\n📊 วัดผล best.pt กับชุด {split} ...")
    metrics = YOLO(str(best_pt)).val(
        data=str(data_yaml),
        split=split,
        imgsz=args.imgsz,
        device=device,
        project=str(save_dir),
        name=f"eval_{split}",
        plots=True,
    )

    print("\n" + "=" * 60)
    print(f"✅ เทรนเสร็จ! ผลกับชุด {split}:")
    print(f"   mAP50     : {metrics.box.map50:.3f}   (หาป้ายเจอและกรอบตรง >= 50% — ยิ่งใกล้ 1 ยิ่งดี)")
    print(f"   mAP50-95  : {metrics.box.map:.3f}   (เข้มขึ้น วัดว่ากรอบแนบป้ายพอดีแค่ไหน)")
    print(f"   Precision : {metrics.box.mp:.3f}   (ที่บอกว่าเป็นป้าย ถูกจริงกี่ %)")
    print(f"   Recall    : {metrics.box.mr:.3f}   (ป้ายทั้งหมด หาเจอกี่ %)")
    print(f"   โมเดล     : {best_pt}")
    print("=" * 60)

    if args.deploy:
        deploy(best_pt)
    else:
        print("\nถ้าพอใจผลแล้ว เอาไปใช้ในระบบได้ 2 วิธี:")
        print(f"   1) copy {best_pt}")
        print(f"      ไปทับ {cfg.PLATE_YOLO_MODEL}")
        print("   2) หรือรันสคริปต์นี้ใหม่พร้อม --deploy")


if __name__ == "__main__":
    # ต้องมี if __name__ == "__main__" เพราะบน Windows ตัวโหลดรูป (workers) จะ import ไฟล์นี้ซ้ำ
    # ถ้าไม่มีบรรทัดนี้ มันจะเริ่มเทรนซ้อนกันไม่รู้จบ
    main()
