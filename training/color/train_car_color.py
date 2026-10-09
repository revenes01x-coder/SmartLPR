"""
train_car_color_model.py
เทรนโมเดลแยกสีรถด้วย EfficientNetB0 (Transfer Learning) จาก VCoR dataset
รันบนเครื่อง local — ใช้ CPU ได้ (ช้ากว่า GPU มาก แต่รันได้)

โครงสร้าง dataset ที่ใช้ (จากที่เช็คไว้ — มีครบ train/val/test):
    archive/train/white/*.jpg, archive/train/black/*.jpg, ...
    archive/val/white/*.jpg,   archive/val/black/*.jpg, ...
    archive/test/white/*.jpg,  archive/test/black/*.jpg, ...
    (15 โฟลเดอร์สีในแต่ละชุด)

วิธีใช้:
    python train_car_color_model.py

ผลลัพธ์ที่ได้หลังรันเสร็จ:
    - car_color_model.h5      -> ไฟล์โมเดล เอาไปแทนที่ COLOR_MODEL_PATH ใน pipeline หลัก
    - class_names.json        -> รายชื่อสีเรียงตาม index ที่โมเดลทาย (ต้องใช้คู่กับ .h5 เสมอ)
    - training_history.png    -> กราฟ accuracy/loss ระหว่างเทรน
    - confusion_matrix.png    -> ดูว่าสีไหนสับสนกับสีไหนบ่อย
"""

import os
import json
import numpy as np
import matplotlib.pyplot as plt
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # ให้ import train_config.py (อยู่ในโฟลเดอร์ training) ได้
import train_config as cfg

import tensorflow as tf
from tensorflow.keras.applications import EfficientNetB0
from tensorflow.keras.applications.efficientnet import preprocess_input
from tensorflow.keras.layers import GlobalAveragePooling2D, Dense, Dropout
from tensorflow.keras.models import Model
from tensorflow.keras.preprocessing.image import ImageDataGenerator
from tensorflow.keras.callbacks import EarlyStopping, ModelCheckpoint, ReduceLROnPlateau

# ==========================================
# ⚙️ CONFIG — path มาจาก training/train_config.py
# ==========================================
DATASET_ROOT = str(cfg.COLOR_DATASET_DIR)
TRAIN_DIR = os.path.join(DATASET_ROOT, "train")
VAL_DIR = os.path.join(DATASET_ROOT, "val")
TEST_DIR = os.path.join(DATASET_ROOT, "test")

# ผลลัพธ์ไปอยู่ training/outputs/color ก่อน ทดสอบแล้วพอใจค่อย copy ไปแทนที่ models/color/ ของระบบจริง
OUTPUT_DIR = str(cfg.COLOR_OUTPUT_DIR)
OUTPUT_MODEL_PATH = os.path.join(OUTPUT_DIR, "car_color_model.h5")
OUTPUT_CLASSNAMES_PATH = os.path.join(OUTPUT_DIR, "class_names.json")

IMG_SIZE = (224, 224)          # ขนาดที่ EfficientNetB0 ต้องการ (พอดีกับที่ pipeline หลักใช้อยู่แล้ว)
BATCH_SIZE = 32

EPOCHS_PHASE1 = 30             # รอบแรก: freeze EfficientNetB0 เทรนแค่ layer ใหม่
EPOCHS_PHASE2 = 20             # รอบสอง (fine-tune): unfreeze บาง layer ท้ายๆ
FINE_TUNE_AT_LAYERS_FROM_END = 30   # EfficientNetB0 มี layer เยอะกว่า MobileNet (~237 vs ~137) เลยปลด freeze มากขึ้นหน่อย


def main():
    print("=" * 60)
    print(" เริ่มเทรนโมเดลแยกสีรถ")
    print("=" * 60)
    os.makedirs(OUTPUT_DIR, exist_ok=True)  # ต้องมีโฟลเดอร์ก่อน ModelCheckpoint จะเซฟโมเดลระหว่างเทรน

    # เช็คก่อนว่า TensorFlow เห็น GPU จริงไหม
    gpus = tf.config.list_physical_devices('GPU')
    print(f"\nGPU ที่เจอ: {gpus}")
    if not gpus:
        print("  ไม่เจอ GPU เลย จะรันบน CPU (ช้ากว่ามาก) เช็คการติดตั้ง CUDA/cuDNN ถ้าคาดว่าควรมี GPU")

    # ==========================================
    # 1) โหลดข้อมูล — ใช้ 3 โฟลเดอร์แยกที่มีอยู่แล้ว (train / val / test)
    #    ไม่ต้องใช้ validation_split เพราะ dataset นี้แบ่งมาให้พร้อมแล้ว
    # ==========================================
    print("\n[1/6] กำลังโหลด dataset...")

    train_datagen = ImageDataGenerator(
        preprocessing_function=preprocess_input,
        rotation_range=15,
        width_shift_range=0.1,
        height_shift_range=0.1,
        horizontal_flip=True,
        zoom_range=0.15,
        brightness_range=[0.7, 1.3],   # ช่วยให้ทนต่อสภาพแสงกลางวัน/กลางคืนได้ดีขึ้น
    )

    # val และ test ไม่ทำ augmentation (ต้องประเมินด้วยภาพจริง ไม่ใช่ภาพที่ถูกดัดแปลง)
    eval_datagen = ImageDataGenerator(preprocessing_function=preprocess_input)

    train_data = train_datagen.flow_from_directory(
        TRAIN_DIR,
        target_size=IMG_SIZE,
        batch_size=BATCH_SIZE,
        class_mode="categorical",
        shuffle=True,
    )

    val_data = eval_datagen.flow_from_directory(
        VAL_DIR,
        target_size=IMG_SIZE,
        batch_size=BATCH_SIZE,
        class_mode="categorical",
        shuffle=False,
    )

    test_data = eval_datagen.flow_from_directory(
        TEST_DIR,
        target_size=IMG_SIZE,
        batch_size=BATCH_SIZE,
        class_mode="categorical",
        shuffle=False,
    )

    # ดึงชื่อ class ตามลำดับ index ที่ Keras กำหนด (เรียงตามชื่อโฟลเดอร์)
    class_indices = train_data.class_indices  # {'beige': 0, 'black': 1, ...}
    class_names = [None] * len(class_indices)
    for name, idx in class_indices.items():
        class_names[idx] = name

    num_classes = len(class_names)
    print(f"-> เจอทั้งหมด {num_classes} สี: {class_names}")
    print(f"-> จำนวนรูป train: {train_data.samples} | validation: {val_data.samples}")

    # ==========================================
    # 2) สร้างโครงสร้างโมเดล (EfficientNetB0 Transfer Learning)
    # ==========================================
    print("\n[2/6] กำลังสร้างโครงสร้างโมเดล...")

    base_model = EfficientNetB0(weights="imagenet", include_top=False, input_shape=(224, 224, 3))
    base_model.trainable = False  # freeze ตอนแรก

    x = GlobalAveragePooling2D()(base_model.output)
    x = Dense(128, activation="relu")(x)
    x = Dropout(0.3)(x)  # กัน overfitting
    output = Dense(num_classes, activation="softmax")(x)

    model = Model(inputs=base_model.input, outputs=output)
    model.compile(
        optimizer="adam",
        loss="categorical_crossentropy",
        metrics=["accuracy"],
    )
    model.summary()

    # ==========================================
    # 3) เทรนรอบแรก (freeze MobileNet)
    # ==========================================
    print(f"\n[3/6] เทรนรอบแรก ({EPOCHS_PHASE1} epochs, freeze base model)...")

    callbacks = [
        EarlyStopping(monitor="val_accuracy", patience=5, restore_best_weights=True),
        ReduceLROnPlateau(monitor="val_loss", factor=0.5, patience=3, min_lr=1e-7),
        ModelCheckpoint(OUTPUT_MODEL_PATH, monitor="val_accuracy", save_best_only=True, verbose=1),
    ]

    history1 = model.fit(
        train_data,
        validation_data=val_data,
        epochs=EPOCHS_PHASE1,
        callbacks=callbacks,
    )

    # ==========================================
    # 4) Fine-tune รอบสอง (ปลด freeze บาง layer ท้ายๆ)
    # ==========================================
    print(f"\n[4/6] Fine-tuning ({EPOCHS_PHASE2} epochs, unfreeze {FINE_TUNE_AT_LAYERS_FROM_END} layers สุดท้าย)...")

    base_model.trainable = True
    for layer in base_model.layers[:-FINE_TUNE_AT_LAYERS_FROM_END]:
        layer.trainable = False

    model.compile(
        optimizer=tf.keras.optimizers.Adam(learning_rate=1e-5),  # LR ต่ำมาก กัน weight เดิมพังตอน fine-tune
        loss="categorical_crossentropy",
        metrics=["accuracy"],
    )

    history2 = model.fit(
        train_data,
        validation_data=val_data,
        epochs=EPOCHS_PHASE2,
        callbacks=callbacks,
    )

    # ==========================================
    # 5) ประเมินผล + กราฟ + confusion matrix
    # ==========================================
    print("\n[5/6] กำลังประเมินผลและสร้างกราฟ...")

    val_loss, val_acc = model.evaluate(val_data)
    print(f"\n✅ Validation Accuracy: {val_acc:.2%} | Loss: {val_loss:.4f}")

    test_loss, test_acc = model.evaluate(test_data)
    print(f"✅ Test Accuracy (ชุดที่ไม่เคยใช้เลยตลอดการเทรน): {test_acc:.2%} | Loss: {test_loss:.4f}")

    # รวม history ทั้ง 2 รอบเข้าด้วยกัน แล้ว plot
    acc_all = history1.history["accuracy"] + history2.history["accuracy"]
    val_acc_all = history1.history["val_accuracy"] + history2.history["val_accuracy"]
    loss_all = history1.history["loss"] + history2.history["loss"]
    val_loss_all = history1.history["val_loss"] + history2.history["val_loss"]

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    axes[0].plot(acc_all, label="Train Accuracy")
    axes[0].plot(val_acc_all, label="Validation Accuracy")
    axes[0].axvline(x=EPOCHS_PHASE1, color="gray", linestyle="--", label="เริ่ม Fine-tune")
    axes[0].set_title("Accuracy ระหว่างเทรน")
    axes[0].set_xlabel("Epoch")
    axes[0].legend()

    axes[1].plot(loss_all, label="Train Loss")
    axes[1].plot(val_loss_all, label="Validation Loss")
    axes[1].axvline(x=EPOCHS_PHASE1, color="gray", linestyle="--", label="เริ่ม Fine-tune")
    axes[1].set_title("Loss ระหว่างเทรน")
    axes[1].set_xlabel("Epoch")
    axes[1].legend()

    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR, "training_history.png"), dpi=120)
    print(f"-> บันทึกกราฟไว้ที่: {OUTPUT_DIR}\\training_history.png")

    # Confusion Matrix — ใช้ test set (ชุดที่ไม่เคยเห็นเลยตลอดกระบวนการเทรน) เพื่อความแม่นยำสุดท้าย
    print("-> กำลังคำนวณ confusion matrix จาก test set (อาจใช้เวลาสักครู่)...")
    test_data.reset()
    y_pred_probs = model.predict(test_data, verbose=0)
    y_pred = np.argmax(y_pred_probs, axis=1)
    y_true = test_data.classes

    from sklearn.metrics import confusion_matrix, classification_report
    cm = confusion_matrix(y_true, y_pred)

    fig2, ax2 = plt.subplots(figsize=(10, 8))
    im = ax2.imshow(cm, cmap="Blues")
    ax2.set_xticks(range(num_classes))
    ax2.set_yticks(range(num_classes))
    ax2.set_xticklabels(class_names, rotation=45, ha="right")
    ax2.set_yticklabels(class_names)
    ax2.set_xlabel("โมเดลทาย")
    ax2.set_ylabel("คำตอบจริง")
    ax2.set_title("Confusion Matrix")
    for i in range(num_classes):
        for j in range(num_classes):
            ax2.text(j, i, cm[i, j], ha="center", va="center",
                     color="white" if cm[i, j] > cm.max() / 2 else "black", fontsize=7)
    plt.colorbar(im)
    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR, "confusion_matrix.png"), dpi=120)
    print(f"-> บันทึก confusion matrix ไว้ที่: {OUTPUT_DIR}\\confusion_matrix.png")

    print("\n" + classification_report(y_true, y_pred, target_names=class_names))

    # ==========================================
    # 6) Save โมเดล + รายชื่อ class
    # ==========================================
    print("\n[6/6] กำลังบันทึกโมเดล...")

    os.makedirs(os.path.dirname(OUTPUT_MODEL_PATH), exist_ok=True)
    model.save(OUTPUT_MODEL_PATH)
    print(f"-> บันทึกโมเดลไว้ที่: {OUTPUT_MODEL_PATH}")

    with open(OUTPUT_CLASSNAMES_PATH, "w", encoding="utf-8") as f:
        json.dump(class_names, f, ensure_ascii=False, indent=2)
    print(f"-> บันทึกรายชื่อ class ไว้ที่: {OUTPUT_CLASSNAMES_PATH}")

    print("\n" + "=" * 60)
    print("✅ เทรนเสร็จสมบูรณ์!")
    print("=" * 60)


if __name__ == "__main__":
    main()