import os
import csv
import glob
import shutil
import tkinter as tk
from tkinter import messagebox
from PIL import Image, ImageTk
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # ให้ import train_config.py (อยู่ในโฟลเดอร์ training) ได้
import train_config as cfg

# ============================================================
# CONFIG
# ============================================================
CROP_FOLDER  = str(cfg.OCR_CROP_DIR)
OUTPUT_CSV   = str(cfg.OCR_LABELS_CSV)
DELETED_FOLDER = str(cfg.OCR_DELETED_DIR)

SUPPORTED_EXT = ['.jpg', '.jpeg', '.png', '.bmp']
DISPLAY_SIZE  = (600, 200)

# ============================================================
# Helper functions
# ============================================================
def load_images(crop_folder):
    files = []
    for ext in SUPPORTED_EXT:
        files += glob.glob(os.path.join(crop_folder, f'*{ext}'))
        files += glob.glob(os.path.join(crop_folder, f'*{ext.upper()}'))
    return sorted(set(files))


def load_existing_labels(output_csv):
    labels = {}
    if os.path.exists(output_csv):
        with open(output_csv, 'r', encoding='utf-8-sig') as f:
            for row in csv.DictReader(f):
                labels[row['filename']] = row['text']
    return labels


def save_labels(output_csv, image_files, labels):
    with open(output_csv, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['filename', 'text', 'done'])
        for img_path in image_files:
            filename = os.path.basename(img_path)
            text     = labels.get(filename, '')
            done     = 'YES' if text.strip() else 'NO'
            writer.writerow([filename, text, done])


def find_resume_index(image_files, labels):
    last_done = -1
    for i, f in enumerate(image_files):
        if labels.get(os.path.basename(f), '').strip():
            last_done = i
    if last_done == -1:
        return 0
    return min(last_done + 1, len(image_files) - 1)


# ============================================================
# GUI
# ============================================================
class LabelingApp:
    def __init__(self, root):
        self.root = root
        self.root.title("🏷️ OCR Labeling Tool")
        self.root.configure(bg='#1e1e1e')
        self.root.resizable(False, False)

        os.makedirs(DELETED_FOLDER, exist_ok=True)

        self.image_files = load_images(CROP_FOLDER)
        self.labels      = load_existing_labels(OUTPUT_CSV)

        if not self.image_files:
            messagebox.showerror("Error", f"ไม่เจอรูปใน:\n{CROP_FOLDER}")
            root.destroy()
            return

        self.total = len(self.image_files)
        self.index = find_resume_index(self.image_files, self.labels)

        self.build_ui()
        self.load_image()

        self.root.bind('<Return>',    lambda e: self.save_only())
        self.root.bind('<Control-s>', lambda e: self.save_csv())
        self.root.bind('<Control-n>', lambda e: self.jump_to_unlabeled())
        self.root.bind('<Prior>',     lambda e: self.prev_image())
        self.root.bind('<Next>',      lambda e: self.next_image())
        self.root.bind('<Delete>',    lambda e: self.delete_image())  # Delete key

    def build_ui(self):
        # Progress
        top_frame = tk.Frame(self.root, bg='#1e1e1e')
        top_frame.pack(fill='x', padx=10, pady=(10, 0))

        self.lbl_progress = tk.Label(
            top_frame, text="", bg='#1e1e1e', fg='#9cdcfe',
            font=('Consolas', 11)
        )
        self.lbl_progress.pack(side='left')

        self.lbl_done = tk.Label(
            top_frame, text="", bg='#1e1e1e', fg='#4ec94e',
            font=('Consolas', 11)
        )
        self.lbl_done.pack(side='right')

        self.progress_canvas = tk.Canvas(
            self.root, height=6, bg='#3c3c3c', highlightthickness=0
        )
        self.progress_canvas.pack(fill='x', padx=10, pady=(4, 0))

        self.lbl_filename = tk.Label(
            self.root, text="", bg='#1e1e1e', fg='#808080',
            font=('Consolas', 9)
        )
        self.lbl_filename.pack(pady=(8, 2))

        # Image
        self.img_canvas = tk.Canvas(
            self.root,
            width=DISPLAY_SIZE[0], height=DISPLAY_SIZE[1],
            bg='#2d2d2d', highlightthickness=1,
            highlightbackground='#454545'
        )
        self.img_canvas.pack(padx=10)

        # Input
        input_frame = tk.Frame(self.root, bg='#1e1e1e')
        input_frame.pack(fill='x', padx=10, pady=10)

        tk.Label(
            input_frame, text="เฉลย :", bg='#1e1e1e', fg='#d4d4d4',
            font=('Cordia New', 14)
        ).pack(side='left', padx=(0, 8))

        self.entry = tk.Entry(
            input_frame,
            font=('Cordia New', 18), width=28,
            bg='#3c3c3c', fg='#ffffff',
            insertbackground='white',
            relief='flat', bd=6
        )
        self.entry.pack(side='left', fill='x', expand=True)
        self.entry.focus()

        # Buttons row 1
        btn_frame = tk.Frame(self.root, bg='#1e1e1e')
        btn_frame.pack(pady=(0, 4))

        tk.Button(
            btn_frame, text="◀  ก่อนหน้า",
            command=self.prev_image,
            bg='#3c3c3c', fg='#d4d4d4',
            font=('Cordia New', 12), relief='flat',
            padx=12, pady=6, cursor='hand2'
        ).pack(side='left', padx=4)

        tk.Button(
            btn_frame, text="⏭  ข้าม",
            command=self.skip_image,
            bg='#5a4a00', fg='#ffd700',
            font=('Cordia New', 12), relief='flat',
            padx=12, pady=6, cursor='hand2'
        ).pack(side='left', padx=4)

        tk.Button(
            btn_frame, text=" บันทึก  [Enter]",
            command=self.save_only,
            bg='#1a4a1a', fg='#4ec94e',
            font=('Cordia New', 13, 'bold'), relief='flat',
            padx=16, pady=6, cursor='hand2'
        ).pack(side='left', padx=4)

        tk.Button(
            btn_frame, text="⏩ ไปรูปที่ยังไม่ได้ทำ  [Ctrl+N]",
            command=self.jump_to_unlabeled,
            bg='#4a1a5c', fg='#d4a0fe',
            font=('Cordia New', 12, 'bold'), relief='flat',
            padx=12, pady=6, cursor='hand2'
        ).pack(side='left', padx=4)

        tk.Button(
            btn_frame, text="บันทึก CSV  [Ctrl+S]",
            command=self.save_csv,
            bg='#1a3a5c', fg='#9cdcfe',
            font=('Cordia New', 12), relief='flat',
            padx=12, pady=6, cursor='hand2'
        ).pack(side='left', padx=4)

        # ปุ่มลบ — แยกแถวให้เด่น
        del_frame = tk.Frame(self.root, bg='#1e1e1e')
        del_frame.pack(pady=(0, 8))

        tk.Button(
            del_frame, text="🗑  ลบรูปนี้  [Del]",
            command=self.delete_image,
            bg='#5c1a1a', fg='#ff6b6b',
            font=('Cordia New', 13, 'bold'), relief='flat',
            padx=20, pady=8, cursor='hand2'
        ).pack()

        # Hint
        tk.Label(
            self.root,
            text="Enter = บันทึก  |  Del = ลบรูป  |  Ctrl+N = ไปรูปที่ยังไม่ได้ทำ  |  PageUp/Down = เลื่อน  |  Ctrl+S = บันทึก CSV",
            bg='#1e1e1e', fg='#555555', font=('Consolas', 9)
        ).pack(pady=(0, 8))

    def load_image(self):
        if self.index < 0 or self.index >= self.total:
            return

        img_path = self.image_files[self.index]
        filename = os.path.basename(img_path)

        done_count = sum(
            1 for f in self.image_files
            if self.labels.get(os.path.basename(f), '').strip()
        )
        self.lbl_progress.config(text=f"รูปที่ {self.index + 1} / {self.total}")
        self.lbl_done.config(
            text=f"✅ เสร็จแล้ว {done_count} ใบ ({done_count/self.total*100:.1f}%)"
        )
        self.lbl_filename.config(text=filename)

        self.progress_canvas.update()
        bar_w = int(self.progress_canvas.winfo_width() * done_count / self.total)
        self.progress_canvas.delete('all')
        self.progress_canvas.create_rectangle(0, 0, bar_w, 6, fill='#4ec94e', outline='')

        try:
            img = Image.open(img_path)
            img.thumbnail(DISPLAY_SIZE, Image.LANCZOS)
            self.tk_img = ImageTk.PhotoImage(img)
            self.img_canvas.delete('all')
            self.img_canvas.create_image(
                DISPLAY_SIZE[0] // 2, DISPLAY_SIZE[1] // 2,
                anchor='center', image=self.tk_img
            )
        except Exception as e:
            self.img_canvas.delete('all')
            self.img_canvas.create_text(
                DISPLAY_SIZE[0] // 2, DISPLAY_SIZE[1] // 2,
                text=f"โหลดรูปไม่ได้: {e}", fill='#ff5555'
            )

        self.entry.delete(0, tk.END)
        existing = self.labels.get(filename, '')
        if existing:
            self.entry.insert(0, existing)
            self.entry.config(bg='#2a3a2a')
        else:
            self.entry.config(bg='#3c3c3c')

        self.entry.focus()

    def delete_image(self):
        """ย้ายรูปไปโฟลเดอร์ Deleted_Plates แล้วไปรูปถัดไป"""
        if self.index < 0 or self.index >= self.total:
            return

        img_path = self.image_files[self.index]
        filename = os.path.basename(img_path)

        confirm = messagebox.askyesno(
            "ยืนยันการลบ",
            f"ต้องการลบรูปนี้ไหม?\n{filename}\n\n(รูปจะถูกย้ายไปโฟลเดอร์ Deleted_Plates)"
        )
        if not confirm:
            return

        # ย้ายไฟล์
        dest = os.path.join(DELETED_FOLDER, filename)
        try:
            shutil.move(img_path, dest)
        except Exception as e:
            messagebox.showerror("Error", f"ลบไม่ได้: {e}")
            return

        # ลบออกจาก list และ label
        self.image_files.pop(self.index)
        self.labels.pop(filename, None)
        self.total = len(self.image_files)

        # บันทึก CSV ใหม่
        self.save_csv(silent=True)

        if self.total == 0:
            messagebox.showinfo("เสร็จสิ้น", "ไม่มีรูปเหลือแล้ว")
            self.root.destroy()
            return

        # ปรับ index ไม่ให้เกิน
        if self.index >= self.total:
            self.index = self.total - 1

        self.load_image()

    def save_only(self):
        text = self.entry.get().strip()
        if not text:
            messagebox.showwarning("แจ้งเตือน", "กรุณาพิมพ์เฉลยก่อนครับ\nถ้าอ่านไม่ออกให้กด 'ข้าม'")
            return
        filename = os.path.basename(self.image_files[self.index])
        self.labels[filename] = text
        self.save_csv(silent=True)
        self.load_image()

    def jump_to_unlabeled(self):
        start = self.index + 1
        for i in range(start, self.total):
            if not self.labels.get(os.path.basename(self.image_files[i]), '').strip():
                self.index = i
                self.load_image()
                return
        for i in range(0, self.index):
            if not self.labels.get(os.path.basename(self.image_files[i]), '').strip():
                self.index = i
                self.load_image()
                return
        messagebox.showinfo("เสร็จสิ้น!", "ทำเฉลยครบทุกรูปแล้วครับ! 🎉")

    def skip_image(self):
        self.next_image()

    def next_image(self):
        if self.index < self.total - 1:
            self.index += 1
            self.load_image()
        else:
            done = sum(1 for f in self.image_files if self.labels.get(os.path.basename(f), '').strip())
            messagebox.showinfo("เสร็จสิ้น!", f"Label ครบทุกรูปแล้วครับ!\n\nเสร็จแล้ว: {done}/{self.total} ใบ")

    def prev_image(self):
        if self.index > 0:
            self.index -= 1
            self.load_image()

    def save_csv(self, silent=False):
        save_labels(OUTPUT_CSV, self.image_files, self.labels)
        if not silent:
            messagebox.showinfo("บันทึกแล้ว", f"บันทึก CSV สำเร็จครับ\n{OUTPUT_CSV}")


# MAIN
if __name__ == "__main__":
    root = tk.Tk()
    app  = LabelingApp(root)
    root.protocol("WM_DELETE_WINDOW", lambda: (app.save_csv(silent=True), root.destroy()))
    root.mainloop()