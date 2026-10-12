#!/usr/bin/env sh
# ============================================================
# สร้าง network กลางที่ทั้ง 3 โปรเจค (backend / camera / frontend) ใช้คุยกัน
# รันซ้ำได้ไม่มีผลเสีย: ถ้ามีอยู่แล้วจะข้าม
#
#   smartlpr_web  = สะพานหน้าบ้าน: frontend <-> backend <-> camera
#   smartlpr_db   = สะพานหลังบ้าน: backend, camera <-> db   (frontend ไม่อยู่ในนี้ = มองไม่เห็น DB)
#
# Windows (PowerShell) ไม่มี sh — ใช้ 2 คำสั่ง docker network create ด้านล่างพิมพ์ตรงๆ ได้เลย
# ============================================================
set -e

create_net() {
    name="$1"; shift
    if docker network inspect "$name" >/dev/null 2>&1; then
        echo "network $name มีอยู่แล้ว — ข้าม"
    else
        docker network create "$@" "$name" >/dev/null
        echo "สร้าง network $name แล้ว"
    fi
}

# ล็อก subnet/gateway ไว้ เพราะ frontend/nginx.conf เชื่อ X-Forwarded-For จาก gateway 172.20.0.1 เท่านั้น
# (ถ้าเปลี่ยนตรงนี้ ต้องแก้ set_real_ip_from ใน nginx.conf ให้ตรงกันด้วย)
create_net smartlpr_web --driver bridge --subnet 172.20.0.0/16 --gateway 172.20.0.1
create_net smartlpr_db --driver bridge
