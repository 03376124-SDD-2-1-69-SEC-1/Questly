<p align="center">
  <img src="docs/Questly-logo.png" alt="Questly" width="480">
</p>

# Questly

<p align="center">
  <img src="https://img.shields.io/badge/PYTHON-3.12%2B-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/FASTAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white" alt="FastAPI">
  <img src="https://img.shields.io/badge/JINJA2-B41717?style=for-the-badge&logo=jinja&logoColor=white" alt="Jinja2">
  <img src="https://img.shields.io/badge/UVICORN-499848?style=for-the-badge&logo=uvicorn&logoColor=white" alt="Uvicorn">
  <img src="https://img.shields.io/badge/UV-DE5FE9?style=for-the-badge&logo=uv&logoColor=white" alt="uv"><br>
  <img src="https://img.shields.io/badge/SQLMODEL-7E56C2?style=for-the-badge" alt="SQLModel">
  <img src="https://img.shields.io/badge/SQLALCHEMY-2.x-D71F00?style=for-the-badge&logo=sqlalchemy&logoColor=white" alt="SQLAlchemy">
  <img src="https://img.shields.io/badge/PSYCOPG-3-336791?style=for-the-badge&logo=postgresql&logoColor=white" alt="psycopg">
  <img src="https://img.shields.io/badge/POSTGRESQL-4169E1?style=for-the-badge&logo=postgresql&logoColor=white" alt="PostgreSQL">
  <img src="https://img.shields.io/badge/NEON-00E599?style=for-the-badge&logo=neon&logoColor=black" alt="Neon">
  <img src="https://img.shields.io/badge/PGVECTOR-768%20dims-336791?style=for-the-badge&logo=postgresql&logoColor=white" alt="pgvector"><br>
  <img src="https://img.shields.io/badge/ALEMBIC-6BA81E?style=for-the-badge&logo=sqlalchemy&logoColor=white" alt="Alembic">
  <img src="https://img.shields.io/badge/PYMUPDF-0A66C2?style=for-the-badge" alt="PyMuPDF">
  <img src="https://img.shields.io/badge/OPENROUTER-6467F2?style=for-the-badge&logo=openrouter&logoColor=white" alt="OpenRouter">
  <img src="https://img.shields.io/badge/CLOUDFLARE%20R2-F38020?style=for-the-badge&logo=cloudflare&logoColor=white" alt="Cloudflare R2"><br>
  <img src="https://img.shields.io/badge/TAILWIND%20CSS-4-06B6D4?style=for-the-badge&logo=tailwindcss&logoColor=white" alt="Tailwind CSS">
  <img src="https://img.shields.io/badge/PYTEST-0A9EDC?style=for-the-badge&logo=pytest&logoColor=white" alt="Pytest">
  <img src="https://img.shields.io/badge/RUFF-D7FF64?style=for-the-badge&logo=ruff&logoColor=black" alt="Ruff">
  <img src="https://img.shields.io/badge/GITHUB%20ACTIONS-2088FF?style=for-the-badge&logo=githubactions&logoColor=white" alt="GitHub Actions">
</p>

**ภาษา:** [English](README.md) | ภาษาไทย

Questly คือแอป FastAPI + Jinja2 สำหรับวิชาเขียนโปรแกรม ผู้สอนร่างโจทย์และ
ชุดทดสอบโดยมี AI ช่วยจาก PDF เอกสารประกอบการสอนของตัวเอง แล้ว publish
ไปยัง Classroom และนักศึกษาส่งโค้ดเพื่อให้ระบบตรวจ

**สถาปัตยกรรม** แอป FastAPI ตัวเดียวใน repo เดียว (modular monolith) โดย
`core/` และ `ai/` เป็นโมดูลของแอปนี้ เรียกกันผ่าน Python interface
(`typing.Protocol`) ไม่มีการเรียกผ่านเครือข่ายระหว่างกัน และ `ai/` ไม่มี route
ของตัวเอง ข้อมูลอยู่ใน Neon Postgres project เดียว แยก schema `core` กับ `rag`
(pgvector, ไม่มี foreign key ข้าม schema) ส่วนไฟล์ PDF อยู่ใน Cloudflare R2
embedding และ generation เรียกผ่าน OpenRouter (ADR-0009) กฎอยู่ใน `AGENTS.md`
flow และการตัดสินใจอยู่ใน `docs/adr/0007-classroom-centric-flow.md` และสถานะ
ปัจจุบันของแต่ละส่วนอยู่ในตาราง Status ของ `AGENTS.md`

## เริ่มต้นใช้งาน

### 1. Clone Repository

```bash
git clone https://github.com/03376124-SDD-2-1-69-SEC-1/PyJudge.git
cd PyJudge
```

หากระบบไม่รู้จักคำสั่ง `git` ให้ติดตั้งตามหัวข้อถัดไป แล้วจึงรันคำสั่ง clone
อีกครั้ง

### 2. ตรวจสอบเครื่องมือที่จำเป็น

โปรเจกต์ต้องใช้ Git, `uv` และ Python 3.12 ขึ้นไป

#### macOS

เปิด Terminal แล้วรัน:

```bash
git --version
uv --version
python3 --version
```

ติดตั้งเครื่องมือที่ยังไม่มี:

```bash
# Git (Apple Command Line Tools)
xcode-select --install

# uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Python 3.12 ที่จัดการโดย uv
uv python install 3.12
```

#### Windows

เปิด PowerShell แล้วรัน:

```powershell
git --version
uv --version
py --version
```

ติดตั้งเครื่องมือที่ยังไม่มีด้วย WinGet:

```powershell
# Git
winget install --id Git.Git -e --source winget

# uv
winget install --id astral-sh.uv -e

# Python 3.12 ที่จัดการโดย uv
uv python install 3.12
```

หลังติดตั้งเครื่องมือให้เปิด Terminal หรือ PowerShell ใหม่ แล้วตรวจสอบเวอร์ชัน
อีกครั้ง สามารถดูวิธีติดตั้งทางเลือกได้จากหน้าอย่างเป็นทางการของ
[Git](https://git-scm.com/install/) และ
[`uv`](https://docs.astral.sh/uv/getting-started/installation/)

### 3. ติดตั้ง Dependencies

รันคำสั่งนี้ภายในไดเรกทอรีของโปรเจกต์:

```bash
uv sync
```

`uv` จะสร้าง virtual environment และติดตั้ง dependencies ตาม lockfile
โดยไม่จำเป็นต้อง activate environment เมื่อใช้คำสั่ง `uv run`

### 4. ตั้งค่า Environment

สร้างไฟล์ `.env` จากไฟล์ตัวอย่าง

บน macOS:

```bash
cp .env.example .env
```

บน Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

เปิดไฟล์ `.env` แล้วกรอก credentials ที่ได้รับจากทีม ปัจจุบันแอปต้องใช้
การเชื่อมต่อ Neon PostgreSQL ใน `DATABASE_URL` และค่า Cloudflare R2 ต่อไปนี้:

```dotenv
DATABASE_URL=postgresql+psycopg://user:password@host/database?sslmode=require
R2_ENDPOINT_URL=
R2_BUCKET_NAME=
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
BASE_URL=https://questly.example.com
```

ตั้ง `BASE_URL` เป็น origin สาธารณะหลักของแอป (ไม่ต้องมี `/` ปิดท้าย)
ต้องขึ้นต้นด้วย `http://` หรือ `https://` และมี host ค่าอื่นจะถูกปฏิเสธตอนแอป
อ่าน config อีเมลใช้ค่านี้สร้างลิงก์เต็ม: `{BASE_URL}/verify?token=...` ในอีเมลยืนยัน
และ `{BASE_URL}/classes/...` ในอีเมลแจ้งเตือน โดยไม่อ่าน `Host` header จาก request
บน production ให้ตั้ง `APP_ENV=production` และ `BASE_URL`; ถ้า
`APP_ENV=production` แต่ไม่ตั้ง `BASE_URL` แอปจะไม่ start
(`MissingConfigurationError`) ส่วน `APP_ENV` ค่าอื่นใช้ `http://localhost:8000`
เป็นค่าเริ่มต้น

กำหนด `OPENROUTER_API_KEY` เพื่อเรียก OpenRouter สำหรับ generation และ embedding
(ADR-0009) ถ้าเว้นว่างแอปจะใช้ stub generation client แทน ส่วน `GENERATION_MODEL`
และ `EMBEDDING_MODEL` ใช้เลือกโมเดล ค่า default อยู่ใน `src/questly/config.py`
ห้าม commit ไฟล์ `.env` หรือใส่ credentials จริงลงใน `.env.example`

#### อีเมล

`EMAIL_BACKEND` เลือกวิธีส่งอีเมลยืนยันและอีเมลแจ้งเตือน:

| `EMAIL_BACKEND` | การทำงาน | ตัวแปรที่ต้องมี |
| --- | --- | --- |
| `stub` (ค่าเริ่มต้นนอก production) | ไม่ส่งอีเมลจริง เขียนอีเมลทั้งฉบับรวมลิงก์ลง log | ไม่มี |
| `smtp` | ส่งผ่านเซิร์ฟเวอร์ SMTP ด้วย standard library | `EMAIL_FROM`, `SMTP_HOST`, `SMTP_USERNAME`, `SMTP_PASSWORD`; เลือกได้ `SMTP_SECURITY` (`starttls` เป็นค่าเริ่มต้น หรือ `ssl`) และ `SMTP_PORT` (587 สำหรับ `starttls`, 465 สำหรับ `ssl`) |
| `resend` | ส่งผ่าน REST API ของ [Resend](https://resend.com) | `EMAIL_FROM`, `RESEND_API_KEY` |

`EMAIL_FROM` เป็นอีเมลเปล่า ๆ หรือ `Questly <noreply@โดเมนของคุณ>` ก็ได้
ถ้า backend ที่เลือกขาดตัวแปร แอปจะไม่ start และข้อความ error บอกชื่อตัวแปรที่ขาดครบทุกตัว
อีเมลเป็น plain text แบบ UTF-8 ส่งแต่ละฉบับหมดเวลาที่ 10 วินาที และไม่ retry

- **production ปฏิเสธ `stub`** ถ้า `APP_ENV=production` แต่ไม่ตั้งหรือตั้ง
  `EMAIL_BACKEND=stub` แอปจะไม่ start ให้ใช้ `smtp` หรือ `resend`
- **โฮสต์บน cloud** Render และ Railway แบบฟรีบล็อกพอร์ต SMTP ขาออก จึงใช้ `resend`
- **ส่งถึง `@kmitl.ac.th`** Resend ส่งหาผู้รับทั่วไปได้เฉพาะจากโดเมนผู้ส่งที่ยืนยันแล้ว
  ให้ยืนยันโดเมนของคุณใน Resend แล้วส่งจากโดเมนนั้น
- **ลิงก์สำหรับตอนพัฒนา** เมื่อใช้ backend `stub` และ `APP_ENV` ไม่ใช่ `production`
  หน้า G-04 จะแสดงลิงก์ยืนยันบนหน้าเพื่อให้กดต่อได้โดยไม่ต้องมีกล่องจดหมาย
  จะไม่แสดงบน production, เมื่อใช้ `smtp`/`resend` และไม่อยู่ใน response ของ API
  ใครที่เข้าถึงแอปได้จะอ่านลิงก์ของอีเมลที่ยังไม่ยืนยันได้ทุกอีเมล จึงห้ามรัน `stub`
  บนโฮสต์ที่คนอื่นเข้าถึงได้ (เช่น staging ที่ใช้ร่วมกัน) ให้ตั้ง `APP_ENV=production` หรือใช้ backend จริงแทน
- **ส่งอีเมลไม่ผ่านไม่ได้แปลว่าสมัครไม่ผ่าน** บัญชีและลิงก์ยังถูกสร้างไว้ หน้า G-04
  บอกว่าส่งอีเมลไม่สำเร็จพร้อมปุ่ม "ส่งอีเมลอีกครั้ง" และ
  `POST /api/v1/auth/verify/resend` ตอบ 503 `email_delivery_failed`

#### การสมัครสมาชิก

หน้า G-03 ให้เลือกว่าสมัครในฐานะใด (ADR-0013):

| ตัวเลือก | อีเมลที่รับ | บัญชี |
| --- | --- | --- |
| บุคคลใน KMITL (ค่าเริ่มต้น) | อีเมลที่ลงท้าย `@kmitl.ac.th` | Student ติ๊ก "I am an instructor" เพื่อขอสิทธิ์อาจารย์จาก Admin ได้ |
| บุคคลภายนอก | อีเมลที่ถูกรูปแบบและไม่ลงท้าย `@kmitl.ac.th` | Student เท่านั้น ขอสิทธิ์อาจารย์ไม่ได้ |

API รับตัวเลือกเดียวกันผ่าน `"external": true` ของ `POST /api/v1/auth/signup`
ระบบไม่เก็บค่าใหม่ โดเมนของอีเมลบอกว่าบัญชีเป็นแบบไหน

ระบบอีเมลของ KMITL กรองอีเมลที่ Questly ส่ง นักศึกษา KMITL จึงอาจไม่เห็นอีเมลยืนยัน
`ONSCREEN_VERIFY_DOMAINS` คือรายชื่อโดเมนอีเมลที่หน้า G-04 แสดงลิงก์ยืนยันเป็นปุ่ม "ยืนยันอีเมล":

```bash
ONSCREEN_VERIFY_DOMAINS=kmitl.ac.th
```

คั่นด้วย comma ไม่สนตัวพิมพ์เล็กใหญ่ และค่าเริ่มต้นว่าง ค่าที่ไม่ใช่โดเมนทำให้แอปไม่ start
โดเมนในรายการเห็นปุ่มในทุก `APP_ENV` รวม production ระบบยังส่งอีเมลตามปกติ
และปุ่มยังแสดงแม้ส่งอีเมลไม่สำเร็จ ลิงก์ไม่อยู่ใน response ของ API

**ความปลอดภัย** ปุ่มนี้ไม่พิสูจน์ว่าผู้สมัครเป็นเจ้าของกล่องอีเมล ใครที่รู้อีเมล `@kmitl.ac.th`
ของคนอื่นสร้างและยืนยันบัญชี Student ด้วยอีเมลนั้นได้ สิทธิ์อาจารย์ยังต้องให้ Admin อนุมัติ
ให้เอาโดเมนออกจาก `ONSCREEN_VERIFY_DOMAINS` เมื่อ KMITL รับอีเมลของเราได้แล้ว

#### การป้องกันด้านความปลอดภัย

ถ้าจะรัน Questly บนเซิร์ฟเวอร์ ให้ทำตาม [docs/deploy.md](docs/deploy.md)
ซึ่งท้ายไฟล์มี checklist ที่ต้องทำให้ครบก่อนส่ง URL ให้คนนอกทีม แอปที่ serve จริง
(`questly.main:app`) มีการป้องกันดังนี้

- **จำกัดจำนวนคำขอ** แต่ละ IP ของ client ส่ง login ได้ 10 ครั้งใน 5 นาที
  สมัครสมาชิกได้ 5 ครั้งต่อชั่วโมง และขอลิงก์ยืนยันใหม่ได้ 5 ครั้งใน 15 นาที
  โดยนับหน้าเว็บกับ endpoint ใน `/api/v1/auth/` รวมกัน เกินแล้วหน้าเว็บได้ G-05
  สถานะ 429 ส่วน API ได้ 429 `rate_limited` ทั้งสองแบบมี `Retry-After`
  ตัวนับอยู่ในหน่วยความจำของ process และเริ่มใหม่เมื่อ restart ถ้าอยู่หลัง proxy
  ให้รัน uvicorn ด้วย `--proxy-headers --forwarded-allow-ips` ที่ระบุเฉพาะ proxy นั้น
  แบบเดียวกับ `docs/deploy.md` ไม่อย่างนั้นทุก client จะใช้ IP ของ proxy ร่วมกัน
  demo และ test ไม่จำกัดจำนวนคำขอ
- **Header** ทุก response มี `X-Content-Type-Options`, `X-Frame-Options: DENY`,
  `Referrer-Policy` และ `Permissions-Policy` หน้า HTML มี `Content-Security-Policy`
  ที่ไม่อนุญาต JavaScript รับ style จากแอปและ Google Fonts และให้ฟอร์มส่งกลับมาที่แอปเท่านั้น
  งานที่จะเพิ่ม JavaScript หรือแหล่งภายนอกอื่นต้องแก้ `CONTENT_SECURITY_POLICY`
  ใน `src/questly/main.py` ด้วย `Strict-Transport-Security` ส่งเมื่อ cookie เป็น
  `Secure` คือทุกที่ยกเว้น demo และ test
- **เอกสาร API** เมื่อ `APP_ENV=production` หน้า `/docs`, `/redoc` และ
  `/openapi.json` ตอบ 404
- **Health check** `/health/db` และ `/health/r2` ตอบ 503 พร้อม
  `database unavailable` หรือ `storage unavailable` สาเหตุจริงเขียนลง log เท่านั้น
- **อีเมลถึง Member เข้าคิว** อีเมลแจ้ง Member เรื่องโจทย์ที่เผยแพร่หรือแก้ไขเข้าคิวที่
  thread เบื้องหลังหนึ่งตัวส่งให้ การเผยแพร่จึงไม่ต้องรอผู้ให้บริการอีเมล อีเมลที่ยังค้างในคิว
  หายเมื่อ process หยุดหรือ restart อีเมลยืนยันตัวตนยังส่งระหว่าง request เหมือนเดิม
  ส่วน demo และ test ส่งทุกฉบับระหว่าง request
- **worker เดียว** ตัวนับคำขอ คิวอีเมล ความคืบหน้าการ generate และลิงก์ยืนยันบนหน้าจอ
  อยู่ในหน่วยความจำของ process จึงต้องรัน uvicorn worker เดียวเท่านั้น

### 5. รัน Database Migrations

```bash
uv run --env-file .env alembic upgrade head
```

### 6. เริ่ม Development Server

```bash
uv run --env-file .env uvicorn questly.main:app --reload
```

เปิด <http://127.0.0.1:8000> ในเบราว์เซอร์ หยุดเซิร์ฟเวอร์ด้วย `Ctrl+C`

## URL ที่ใช้งานบ่อย

| URL | รายละเอียด |
|---|---|
| <http://127.0.0.1:8000/> | หน้าแรก |
| <http://127.0.0.1:8000/docs> | เอกสาร API แบบโต้ตอบ |
| <http://127.0.0.1:8000/api/v1/topics> | Topics API |
| <http://127.0.0.1:8000/health> | สถานะแอปพลิเคชัน |
| <http://127.0.0.1:8000/health/db> | สถานะ Neon PostgreSQL |
| <http://127.0.0.1:8000/health/r2> | สถานะ Cloudflare R2 |

## คำสั่งสำหรับพัฒนา

รันชุดทดสอบ:

```bash
uv run --env-file .env pytest
```

ตรวจสอบ lint และรูปแบบโค้ด:

```bash
uv run ruff check .
uv run ruff format --check .
```

สร้าง migration หลังแก้ไข database models:

```bash
uv run --env-file .env alembic revision --autogenerate -m "describe the change"
```

### ตรวจ UI ด้วย Playwright (screenshot และวิดีโอ)

`playwright/ui_walk.py` เปิดหน้าของ demo app ใน Chromium จริงที่ความกว้าง desktop
(1280×800) และ mobile (390×844) แล้วเก็บ screenshot เต็มหน้าของทุกหน้า พร้อมวิดีโอ
ของการเดินทั้งหมด ใช้ตรวจการแก้ UI โดยไม่ต้องคลิกเอง และแนบ screenshot ใน PR

Playwright ไม่ใช่ dependency ของโปรเจกต์ `uv run --with playwright` ติดตั้งให้เฉพาะ
คำสั่งนั้น จึงไม่แก้ `pyproject.toml` และ `uv.lock` รันทั้งสองคำสั่งจาก root ของ repo

```bash
# terminal 1 — demo server (ใช้ in-memory fakes ไม่ต้องมีฐานข้อมูล)
uv run python -m scripts.demo
```

```bash
# terminal 2 — ครั้งแรกเท่านั้น: ดาวน์โหลด browser (อาจใช้เวลาหลายนาที)
uv run --with playwright playwright install chromium

# เดินหน้าเริ่มต้น (/classes, /documents)
uv run --with playwright python -I playwright/ui_walk.py

# เดินเฉพาะหน้าที่แก้
uv run --with playwright python -I playwright/ui_walk.py /classes /admin

# T-06: อัปโหลด PDF ตัวอย่างด้วย แล้วถ่ายผลลัพธ์
uv run --with playwright python -I playwright/ui_walk.py --upload-pdf

# เปิดผลลัพธ์บน macOS
open playwright/out
```

| ตัวเลือก | ค่าเริ่มต้น | ความหมาย |
|---|---|---|
| `PATH ...` | `/classes /documents` | หน้าที่จะเปิด เรียงตามลำดับ |
| `--as EMAIL` | `somchai.p@kmitl.ac.th` (Instructor) | บัญชี demo จากรายการ "log in as" ที่ `/login` เช่น `admin@kmitl.ac.th` |
| `--base-url URL` | `http://127.0.0.1:8000` | เซิร์ฟเวอร์ที่จะเดิน |
| `--out DIR` | `playwright/out` | ที่เก็บ screenshot และวิดีโอ |
| `--upload-pdf` | ปิด | อัปโหลด PDF ตัวอย่างที่ `/documents` (T-06) หลังเดินเสร็จ |

ผลลัพธ์อยู่ใน `playwright/out/` ซึ่งอยู่ใน `.gitignore`:

- `desktop-<page>.png` และ `mobile-<page>.png`: screenshot เต็มหน้า หน้าละหนึ่งไฟล์
- `desktop-documents-after-upload.png` และ `mobile-…`: เมื่อใช้ `--upload-pdf`
- `video-desktop/*.webm` และ `video-mobile/*.webm`: วิดีโอของการเดินทั้งหมด

สิ่งที่ควรดู:

- Desktop: แถวที่ควรอยู่เคียงกันต้องไม่ซ้อนเป็นแนวตั้ง ถ้าซ้อน แปลว่า Tailwind class
  อาจยังไม่อยู่ใน `app.css` ให้ rebuild ใหม่ (ดู "Styles" ใน README ภาษาอังกฤษ)
- Mobile: ไม่มีอะไรล้นออกด้านข้าง และไม่มีปุ่มถูกตัด
- สี การ์ด badge และหัวหน้าเพจตรงกับหน้าอื่น

`-I` รัน Python แบบแยกจากโฟลเดอร์ repo สคริปต์จึงเห็นเฉพาะแพ็กเกจ `playwright`
ที่ติดตั้ง ดูรายละเอียดเพิ่มที่ `playwright/README.md`

## โครงสร้างโปรเจกต์

```text
src/questly/
├── main.py             # composition root: สร้าง adapter กับ service แล้ว mount router
├── config.py           # ค่าตั้งที่อ่านจาก environment
├── api_errors.py       # รูปแบบ error JSON ที่ทุก route ใช้ร่วมกัน
├── core/               # slice: topics (ตัวอ้างอิง), auth, classrooms, assignments,
│                       #   generation, submissions, uploads, notifications, admin
├── ai/                 # โมดูล AI ไม่มี route ของตัวเอง: vector storage (app/),
│                       #   ingestion/, embeddings/, retrieval/, client.py
├── integrations/       # adapter ของบริการภายนอก (Judge0, email, generation jobs)
├── database/           # SQL adapter (core/, rag/), R2 (storage/), health check
└── web/                # Jinja templates และ static assets

alembic/                # migration history เดียวสำหรับทั้งสอง schema
scripts/                # demo mode, seed, ตัวรัน integration test, task scope
tests/                  # unit, integration, architecture, contracts, fakes, db, r2
```

## เอกสารอ้างอิงโครงสร้างข้อมูล

เอกสารอ้างอิงโครงสร้าง database layer ของโปรเจกต์ — เก็บ *สถานะปัจจุบัน*
ของโค้ด ส่วนใครแก้ไขได้บ้าง ดู `AGENTS.md` หัวข้อ "Who may change database
and locked files"

### 1. Stack ที่ใช้จริง

**ใช้ ORM ตัวเดียว ไม่ใช่สองตัว** — เป็นความเข้าใจผิดที่เจอบ่อย

```text
┌─────────────────────────────────────────┐
│  SQLModel                               │  ← ที่เราเขียน model
│  (Field, Relationship, SQLModel)        │
├─────────────────────────────────────────┤
│  SQLAlchemy Core + ORM                  │  ← ตัวจริงที่คุยกับ DB
│  (Column, BigInteger, ForeignKey, ...)  │
├─────────────────────────────────────────┤
│  psycopg v3                             │  ← driver
├─────────────────────────────────────────┤
│  PostgreSQL (Neon) + pgvector extension │
└─────────────────────────────────────────┘
```

| ตัว | คืออะไร | ทำไมต้องมี |
|---|---|---|
| **SQLModel** | ชั้นบางๆ ที่ครอบ SQLAlchemy + Pydantic | เขียน model สั้นลง ได้ validation ฟรี |
| **SQLAlchemy** | ORM/Core ตัวจริง | SQLModel ไม่ได้ห่อทุกอย่าง ของที่ขาดต้องเรียกตรง |
| **pgvector** | **ไม่ใช่ ORM** — extension + column type | ให้ SQLAlchemy รู้จักชนิด `VECTOR(768)` |
| **psycopg v3** | database driver | ตัวส่ง SQL จริงไป Postgres |

ทำไมโค้ดถึง import จาก sqlalchemy ตรงๆ:

```python
from sqlmodel import Field, Relationship, SQLModel       # ชั้นบน
from sqlalchemy import Column, BigInteger, ForeignKey    # ของที่ SQLModel ไม่ได้ห่อ
from sqlalchemy.dialects.postgresql import JSONB         # ชนิดเฉพาะ Postgres
from pgvector.sqlalchemy import Vector                   # ชนิดจาก extension
```

ไม่ได้แปลว่าใช้ ORM ปนกัน — เป็น stack เดียวกันคนละชั้นเท่านั้น

**ผลที่ตามมาใน `.env`:** เพราะตัวที่อ่าน connection string คือ SQLAlchemy
(ไม่ใช่ SQLModel) จึงต้องระบุ driver เป็น `postgresql+psycopg://`
ไม่ใช่ `postgresql://` ที่ Neon ก็อปมาให้ — ไม่งั้นมันจะไปหา `psycopg2`
(ตัวเก่า) ที่ไม่ได้ติดตั้งไว้

### 2. โครงสร้างโฟลเดอร์

```text
src/questly/
├── core/                        ← โมดูลของแอป ห้าม import ORM และห้าม import `ai`
│   ├── topics/                  ✅ reference slice — ทำตามรูปนี้
│   │   ├── models.py            dataclass(frozen=True, slots=True)
│   │   ├── ports.py             typing.Protocol
│   │   ├── service.py           sync ล้วน รับ repo ผ่าน constructor
│   │   └── routes.py            sync def, ดึง service จาก app.state
│   └── assignments/ …           รูปเดียวกัน SQL adapter อยู่ใน database/core/
│
├── database/                    ← ของพายคนเดียว ที่เดียวที่ import ORM ได้
│   │                              (ดู AGENTS.md หัวข้อ "Who may change database and locked files")
│   ├── __init__.py              import tables ทั้ง 2 schema (สำคัญกับ alembic)
│   ├── session.py               engine + session factory (sync)
│   ├── core/
│   │   ├── tables.py            SQLModel — schema `core`
│   │   └── *_repository.py      SQL adapter หนึ่งตัวต่อหนึ่ง port ของ core
│   ├── rag/
│   │   ├── tables.py            SQLModel — schema `rag`
│   │   └── vector_repository.py SQL adapter ของ ai/app/repository.py
│   └── storage/r2.py            adapter ของ object storage
│
├── ai/                          ← โมดูล AI ในแอปเดียวกัน เรียกผ่าน port ไม่มี route
└── web/                         ← Jinja2 templates

alembic/                         ← root ตาม convention
├── env.py                       target_metadata = SQLModel.metadata
│                                include_schemas=True
│                                ใช้ DATABASE_URL_UNPOOLED
└── versions/
```

กฎเหล็ก: ทิศทางการ import

```text
core/assignments/models.py      (dataclass)
core/assignments/ports.py       (Protocol)
            ↑ implement โดย ↓
database/core/assignment_repository.py   ← adapter ของ tech lead อยู่ตรงนี้เท่านั้น
            ↓ ใช้ ↓
database/core/tables.py         (SQLModel)
```

Adapter ต้องอยู่ฝั่ง `database/` เพราะมันต้อง import ทั้ง SQLModel และ dataclass
— จุดเชื่อมนี้ห้ามอยู่ฝั่ง `core/`

เรื่อง alembic ที่พลาดกันบ่อย: `--autogenerate` เห็นเฉพาะ table class ที่
**ถูก import แล้ว** เท่านั้น วางไฟล์ไว้เฉยๆ ไม่พอ ต้อง import ใน
`database/__init__.py`:

```python
from questly.database.core import tables as core_tables  # noqa: F401
from questly.database.rag import tables as rag_tables    # noqa: F401
```

ถ้าลืม มันจะไม่ error — แต่จะ generate migration ที่ขาดตารางไปเงียบๆ
และรอบถัดไปอาจสั่ง `drop_table` ตารางที่มีอยู่จริงใน DB **เปิดไฟล์ migration
ตรวจทุกครั้งก่อนรัน**

### 3. ERD

> แผนภาพนี้เก่ากว่า schema ของ Classroom ตาม ADR-0007 และ ADR-0008
> แหล่งความจริงคือ `database/core/tables.py` และ `database/rag/tables.py`

```mermaid
erDiagram
    direction TB

    %% ==================== schema: core ====================

    users {
        BIGSERIAL id PK
        TEXT email UK "index"
        TEXT display_name "nullable"
        TEXT role "CHECK: instructor | admin, default instructor"
        TIMESTAMPTZ created_at "default now()"
        TIMESTAMPTZ updated_at "default now(), onupdate"
    }

    knowledge_documents {
        BIGSERIAL id PK
        TEXT r2_object_key UK "natural key ใช้ cross-check ตอนคัดลอก"
        TEXT filename
        TEXT content_hash "sha256 กันไฟล์ซ้ำ"
        TEXT status "CHECK: uploaded | ingesting | ready | failed"
        JSONB metadata "topic, difficulty, course"
        BIGINT uploaded_by FK "nullable, ON DELETE SET NULL"
        TIMESTAMPTZ created_at
        TIMESTAMPTZ updated_at
    }

    generation_requests {
        BIGSERIAL id PK
        TEXT prompt
        JSONB filters "topic, difficulty — ต้อง match key ใน metadata"
        TEXT status "CHECK: pending | generating | completed | failed"
        TEXT error_code "nullable"
        BIGINT requested_by FK "nullable, ON DELETE SET NULL"
        TIMESTAMPTZ created_at
        TIMESTAMPTZ updated_at
    }

    generation_artifacts {
        BIGSERIAL id PK
        BIGINT request_id FK "NOT NULL, ON DELETE CASCADE"
        JSONB draft "title, statement, test_cases — ไม่มี default"
        JSONB citations "chunk_id, source_id, page, score, text_snapshot"
        TEXT model_provider "nullable"
        TEXT model_name "nullable, เช่น poolside/laguna-s-2.1:free"
        TEXT review_status "CHECK: pending | applied | discarded"
        TIMESTAMPTZ created_at
        TIMESTAMPTZ updated_at
    }

    assignments {
        BIGSERIAL id PK
        BIGINT artifact_id FK,UK "nullable ถ้าสร้างมือ, ON DELETE SET NULL"
        TEXT title
        TEXT problem_statement
        TEXT difficulty "CHECK: easy | medium | hard"
        JSONB metadata "topic, language, course"
        TIMESTAMPTZ created_at
        TIMESTAMPTZ updated_at
    }

    test_cases {
        BIGSERIAL id PK
        BIGINT assignment_id FK "NOT NULL, ON DELETE CASCADE"
        TEXT input_data
        TEXT expected_output
        BOOLEAN is_hidden "default false"
        INT order_index "default 0"
        TIMESTAMPTZ created_at
        TIMESTAMPTZ updated_at
    }

    %% ==================== schema: rag ====================

    knowledge_sources {
        BIGSERIAL id PK
        BIGINT core_document_id UK "logical link ไป core ไม่มี FK จริง"
        TEXT r2_object_key "ซ้ำกับ core ตั้งใจ ใช้ verify link"
        TEXT content_hash
        TEXT status "CHECK: pending | processing | ready | failed"
        TEXT embedding_model "nullable"
        INT embedding_dim "nullable"
        JSONB metadata "GIN index — คัดลอกมาเพื่อ filter"
        TIMESTAMPTZ created_at
        TIMESTAMPTZ updated_at
    }

    knowledge_chunks {
        BIGSERIAL id PK
        BIGINT source_id FK "NOT NULL, ON DELETE CASCADE"
        INT chunk_index "ลำดับใน source, index คู่กับ source_id"
        INT page "nullable"
        TEXT text
        INT token_count "nullable, คุม context budget"
        TEXT content_hash "UNIQUE คู่กับ source_id"
        TEXT embedding_model "ซ้ำจาก source ตั้งใจ กัน vector ปนรุ่น"
        JSONB metadata "question_no, content_type"
        VECTOR embedding "768 มิติ, nullable — HNSW เขียนมือใน migration"
        TIMESTAMPTZ created_at
        TIMESTAMPTZ updated_at
    }

    %% ==================== relationships ====================

    users ||--o{ knowledge_documents : "uploads"
    users ||--o{ generation_requests : "creates"
    generation_requests ||--o{ generation_artifacts : "produces"
    generation_artifacts ||--o| assignments : "applied to"
    assignments ||--o{ test_cases : "has"
    knowledge_sources ||--o{ knowledge_chunks : "split into"
    knowledge_documents ||..o| knowledge_sources : "คัดลอกข้าม schema (ไม่มี FK จริง)"
```

### 4. สิ่งที่ ORM สร้างให้ไม่ได้ — ต้องเขียนมือใน migration

**4.1 ก่อน migration แรก:**

```sql
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS rag;
CREATE EXTENSION IF NOT EXISTS vector;
```

**4.2 HNSW index (สำคัญที่สุด)** — autogenerate ไม่สร้างให้
ต้องเขียนเองใน `op.execute()`:

```sql
CREATE INDEX ON rag.knowledge_chunks
USING hnsw (embedding vector_cosine_ops);
```

ไม่มี → sequential scan ทั้งตาราง ตอนข้อมูลน้อยไม่รู้สึก
แต่หลักหมื่น chunk ขึ้นไปจะช้าทันที

**4.3 Cross-schema cleanup ไม่มี cascade.** ลบ `core.knowledge_documents`
แล้ว `rag.knowledge_sources` + `knowledge_chunks` **ไม่หายตาม**
เพราะไม่มี FK จริง ต้องเขียน cleanup ระดับ application (Core เรียก
use case ลบของโมดูล AI ผ่าน Python interface) ไม่งั้นจะมี orphan chunk
ที่ยัง retrieve เจอ ทั้งที่ไฟล์ต้นฉบับถูกลบแล้ว

### 5. Decision ที่ปิดแล้ว

| เรื่อง | สรุป | ผลกับโค้ด |
|---|---|---|
| `assignments.artifact_id` UNIQUE | **ใส่** | `unique=True` บน Column + `uselist=False` บน relationship |
| Alembic history | **รวม core+rag ไว้ด้วยกัน** ไม่แยก repo, single node | `database/__init__.py` ต้อง import tables ทั้ง 2 ฝั่ง |
| DB ของ teammate | **ใช้ตัวเดียวกัน** | ต้องมีข้อตกลงเรื่อง migration (ดู 5.1) |

**5.1 ใช้ DB ตัวเดียวกัน — สิ่งที่ต้องตกลงเพิ่ม.** ทีม 2 คนใช้ database
เดียวกันไม่ผิดสำหรับ MVP แต่มี 3 เคสที่จะเจอแน่ๆ ถ้าไม่คุยกันก่อน:

1. **`alembic downgrade` กระทบอีกคนทันที** — `alembic/` และ `database/` เป็นของ
   tech lead คนเดียว (AGENTS.md หัวข้อ "Who may change database and locked
   files") ดังนั้นคนรัน downgrade จะมีแค่เราคนเดียว — แต่ต้องบอกก่อนรัน
   ไม่งั้นแอปฝั่ง teammate จะพังกลางคันโดยเขาไม่รู้สาเหตุ
2. **Test data ปนกัน** — ถ้า teammate เขียน CRUD แล้วลอง insert/delete
   assignment ข้อมูลจะปนกับของเรา ทางแก้ง่ายสุดคือตกลง convention เช่น
   ใส่ prefix ในชื่อ title ตอนเทส หรือใช้ Neon branch แยกเฉพาะตอนรัน test
3. **ถ้าเปลี่ยนใจอยากแยกทีหลัง** — Neon มีฟีเจอร์ **branch** ที่ copy
   database ทั้งก้อนได้ในไม่กี่วินาที (copy-on-write ไม่เปลืองที่)
   ถ้าวันไหนเริ่มชนกันบ่อย แยก branch ให้ teammate ใช้เวลาไม่กี่นาที
   ไม่ต้องตัดสินใจตอนนี้

ทั้งสองข้อบันทึกแล้ว: migration history เดียวใน ADR-0004 และ repo กับแอปเดียวใน
ADR-0005

### 6. ที่ยังเปิดอยู่

| เรื่อง | สถานะ |
|---|---|
| Embedding model | ตัดสินแล้ว: `google/gemini-embedding-001` ผ่าน OpenRouter ที่ 768 มิติ (ADR-0009) `VECTOR(768)` ยังเป็น one-way door เปลี่ยนมิติ = migrate ทั้งตารางและ embed ทุก chunk ใหม่ |

## เทคโนโลยีที่ใช้

- Python 3.12+
- FastAPI และ Uvicorn
- SQLModel, SQLAlchemy และ Alembic
- Neon PostgreSQL และ pgvector
- OpenRouter (SDK `openrouter`) สำหรับ embedding และ generation
- Cloudflare R2 และ Boto3
- Jinja2
- Pytest และ Ruff
- `uv` สำหรับจัดการ Python และ dependencies
