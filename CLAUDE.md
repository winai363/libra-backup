# Libra — กฎถาวร (อ่านก่อนแตะ Libra/KDP ทุกครั้ง)

อ่าน `memory.md` (ท้ายไฟล์ = ล่าสุด) ก่อนเริ่มงานเสมอ. กฎที่ห้ามละเมิด:

## ⛔⛔ TOTAL KDP FREEZE (2 ส.ค. 2026 — บุ๋ยสั่ง "ห้ามเกิดปัญหานี้") — กฎนี้อยู่เหนือทุกกฎด้านล่าง
Amazon บล็อก ebook อีก 2 เล่มวันเดียว (TDAH ES เล่ม1 B0H6TZNC4K + ADHS DE B0H6H2D17K) → **บล็อกสะสมทั้งบัญชี = 4 เล่ม** เสี่ยงโดนปิดทั้งบัญชี (40 เล่ม) สูงมาก. บทเรียน: เล่ม DE เนื้อหาแก้สะอาดแล้ว+ใส่ AI disclosure ถูกต้อง **ก็ยังโดน**; เล่ม ES โดนจากแค่กดเปลี่ยนราคา $0.99 (เนื้อหาใหม่ไม่เคยถูกอัปโหลด) ⇒ ทุก action ที่ trigger content review = แทงหวยเสี่ยงปิดบัญชี ไม่เกี่ยวกับคุณภาพเนื้อหา
1. ⛔ **ห้าม republish / เปลี่ยนราคา / แก้ metadata / อัปโหลดเนื้อหา** เล่มใดๆ บน KDP — ต่อให้บุ๋ยเคยอนุมัติหลักการไว้ ต้องเตือนความเสี่ยงบล็อก+ปิดบัญชีก่อนทุกครั้งและรอคำยืนยันใหม่
2. ⛔ ห้าม appeal / reply อีเมลบล็อก / resubmit เล่มที่โดนบล็อก (ทั้ง 4 เล่ม)
3. ⛔ ห้ามอัปโหลดหนังสือใหม่เข้า KDP บัญชีนี้
4. เลน "ปลอดภัย" ด้านล่าง (free promo / ราคา / A+) **ถูกระงับทั้งหมด** — ราคาก็พิสูจน์แล้วว่า trigger review ได้ (ES 2 ส.ค.)
5. โหมดปัจจุบัน = **PASSIVE MODE ถาวร**: เล่มที่เหลือ ~38 ขายเอง, งบ/แรง = 0, ห้าม unpublish, cron ที่เหลือ = read-only เท่านั้น (sales sync / bookshelf roster / session ensure / รายงาน). ก่อนเปิด cron ใดๆ คืน เช็ค memory `libra-blocked-adhd-books-20260802` ก่อน
6. แผน expert review 2 ส.ค. (ส.ค. ammo month / Gate 31 ส.ค. / October Play) **ยกเลิกทั้งหมด** — ห้ามติดตั้ง gate_20260831.py หรือ cron ใหม่ใดๆ ของแผนนั้น; Ebrolis + LovelyBooks push ยกเลิก

### ด่านบังคับใช้ในโค้ด + เลน staging ที่อนุญาต (22 ส.ค. 2026)

`kdp_freeze.py` = **source of truth ที่รันได้จริง** ไม่ใช่แค่ข้อความเตือน. ทุกทางที่ยิง KDP ได้ถูกปิดหมด:
- Python: `upload_to_kdp / update_cover / update_metadata / update_ebook_content / finish_publish` + ตัวมิวเทตอื่นทั้งหมด (`aplus_upload`, `set_price`, `free_promo_auto.schedule_one`, `kdp_unpublish`, `kdp_live_replace`, `kdp_fix_book/publish`, `kdp_paperback_upload`, `kdp_enroll_v2`, `author_photo/url`) → `KDPFrozenError` ก่อนแตะไฟล์/เปิดบราวเซอร์
- HTTP: `approve-kdp`, `request-approval`, `status=ready` → **423** (`archived` ยังทำได้ เพราะเป็น local)
- Shell: `scripts/process_kdp_queue.sh` → **exit 73** ก่อนอ่านคิว; `watchdog.sh` ไม่ตั้ง `ready` อีกแล้ว (ใช้ `staged_freeze` + `publish_blocked`)
- `scripts/kdp_action_executor.py::validate_action` ปฏิเสธทุก action ด้วยเหตุผล `total_kdp_freeze` (กฎรายชนิดย้ายไป `validate_action_rules` ที่เข้าถึงได้ผ่านด่านเท่านั้น)
- ⛔ ห้ามเพิ่ม force flag / env override / วันหมดอายุ / approval token — ปลด freeze ต้องแก้ซอร์สและรีวิว

เลนที่อนุญาต (เตรียมอย่างเดียว ไม่ publish):
- `python3 scripts/prepare_kdp_pilot.py --dry-run` และ `--execute` เมื่อบุ๋ยสั่งเท่านั้น — เขียนไฟล์เฉพาะใต้ `/root/kdp-staging/`
- ผลลัพธ์สำเร็จ = `staged_quality_passed` + `publish_blocked: total_kdp_freeze` พร้อม `staging-manifest.json`
- staging ห้ามเติม `queue.txt`, ห้ามตั้ง `ready/uploaded/live`, ห้ามเปิด Playwright, ห้ามคุย KDP (มีเทสต์กันไว้)
- นิช visual: ไม่ผ่านด่านถ้าไม่มีภาพสาธิต ≥12 รูป + `image-provenance.json` ครบทุกรูป (`validate_book(..., require_visuals=True)`)

## 🚫 ห้าม republish เล่มที่เคย publish แล้ว (มี ASIN)
ทุกการ republish (เปลี่ยนหมวด/subtitle/description/ปก/เนื้อใน) = ส่งเล่มกลับเข้า Amazon content review ใหม่ = ทอยลูกเต๋า. **acuarela ถูก reject "disappointing customer experience" + หลุดจากร้าน (404) เมื่อ 11 ก.ค. 2026 จากการ republish เพื่อเปลี่ยนหมวดเท่านั้น.** ปกเคยผ่าน (3 ก.ค.) ≠ การันตีว่าจะผ่านอีก.
- Gate ในโค้ด: `scripts/kdp_action_executor.py::validate_action` refuse `category_update` ทุก listing ที่มี `asin` — ห้ามถอย gate นี้
- งานที่พับเก็บเพราะกฎนี้ (11 ก.ค.): subtitle rewrite 12 เล่ม, cover regen 39 เล่ม — ห้ามหยิบมาทำโดยไม่มีคำสั่งบุ๋ยชัดๆ

## 🔴 บัญชี KDP มี content block สะสม 4 ครั้ง — ปกป้องบัญชีมาก่อนทุกเล่ม
1. `high-protein-meal-plan-french` (นิช diet — NO-GO gate มีแล้ว)
2. `acuarela-para-principiantes-guia-paso-a-paso` (11 ก.ค. 2026 — บุ๋ยตัดสิน: ปล่อยตาย **ห้าม appeal / ห้าม resubmit**)
3. `adhd-self-help-adults-es` เล่ม 1 (2 ส.ค. 2026 — โดนจากการเปลี่ยนราคา; paperback ยัง LIVE)
4. `adhd-workbook-german-adults` (2 ส.ค. 2026 — โดนทั้งที่เนื้อหาแก้แล้ว)

block ครั้งถัดไปเสี่ยงระดับปิดบัญชี. ห้ามทำอะไรที่เพิ่มโอกาสโดน review โดยไม่จำเป็น.

## ✅ เลนที่ปลอดภัย (ไม่ trigger content review)
- **Free promo / Countdown** — หน้า promotion-manager (`scripts/free_promo_auto.py`; โหมด manual: `--only <slug> --force --start YYYY-MM-DD --days N`, dry-run ก่อนเสมอ)
  - **กฎ pairing (14 ก.ค. 2026):** free promo ต้องมีคู่ช่องทางขยาย traffic เสมอ — ประกาศใน `data/promo_pairings.json` หรือมีคิวโพสต์ใน `data/reddit_promo_schedule.json` (executor refuse ถ้าไม่มี) เหตุผล: วัดจริง 17 โปรโม → 13 เล่มที่แจกเดี่ยวๆ ได้ 0 downloads และโควตาฟรีมีแค่ 5 วัน/เล่ม/เทอม เมื่อ executor schedule โปรโมสำเร็จจะเติมคิวเตือน Reddit ให้อัตโนมัติ
  - **ก่อนเสนอ/นัด promo เล่มไหน เช็คหน้า promotion-manager จริงก่อนเชื่อ listing.json** — เคยเจอเล่มที่โปรโมไปแล้ว 4-6 ก.ค. แต่ listing ไม่มีบันทึก (ai-creative-workbook-italian) ทำให้เกือบแจกซ้ำ
- **ราคา** — หน้า pricing ตรง (`scripts/set_price.py` มี SAFETY GATE royalty 35% ค้างระหว่างโปรโม — ห้าม publish ถ้า gate abort)
- **A+ Content** — ระบบแยก ไม่แตะตัวเล่ม
- **Ads / external traffic** — ไม่แตะตัวเล่ม (แต่ ads ติดเงื่อนไข checkpoint 31 ก.ค. + เพดานงบ)

## 📕 กฎเนื้อหา
- นิช visual (สอนวาด/ทำอาหาร/งานฝีมือ/คู่มือมีภาพประกอบ) **ต้องมีภาพสาธิตจริงในเนื้อ** ก่อน publish — text-only ในนิชภาพ = นิยาม "disappointing customer experience" ของ Amazon
- นิช diet/meal-plan = NO-GO ถาวร

## โหมดปัจจุบัน (2 ส.ค. 2026 →) — PASSIVE MODE ถาวร
~~90-day profit mode (11 ก.ค.)~~ ถูกแทนด้วย TOTAL KDP FREEZE ด้านบน: ไม่มีเลน experiment ใดๆ เหลือ (free_promo/price_update ระงับหมด), profit agent เหลือแค่ DB bookkeeping, cron ที่รันได้ = read-only เท่านั้น.

## กฎ Autonomous Management (บุ๋ยยืนยัน 18 ก.ค. 2026)

**อะไรทำ auto ได้ให้ทำ แต่ต้องมั่นใจและเห็นผลจริง ห้ามเดา**

- Auto ได้เมื่อ input มาจากหลักฐานตรวจสอบได้ และผลลัพธ์มี `verified_state_change`, KDP/API response, report/transaction จริง หรือ external `post_url`/`post_id` ที่ตรวจย้อนกลับได้
- การคำนวณ, จัดอันดับ, forecast และข้อเสนอทำ auto ได้ แต่ต้องแยก verified fact ออกจาก inference และแสดง data freshness/confidence
- External action จะนับว่า `executed` ได้ต่อเมื่อมีหลักฐานผลลัพธ์จริง ห้ามนับ reminder, digest, planned queue, browser click หรือ process exit code อย่างเดียวเป็นความสำเร็จ
- งานที่ไม่มี API ให้ใช้ browser automation แบบ Claude for Chrome/Playwright: เปิดหน้าจริง → อ่านสถานะก่อนทำ → คลิก/กรอก → รอผล → อ่านสถานะหลังทำและเก็บหลักฐาน before/after; ห้ามใช้ข้อมูลในไฟล์แทนหน้าจริงเมื่อหน้าจอเป็น source of truth
- Browser action ต้องบันทึก URL/หน้าที่ทำ, ค่าก่อนทำ, ค่าหลังทำ และ confirmation/status ที่หน้าเว็บแสดง (รวม screenshot เมื่อจำเป็น) การคลิกสำเร็จทางเทคนิคอย่างเดียวไม่ใช่ผลลัพธ์ธุรกิจ
- ถ้าต้องเดา, ข้อมูล stale/incomplete, ติด OTP/CAPTCHA/login, หรือยืนยัน before/after ไม่ได้ → หยุดเป็น `manual_required`/`insufficient_data` และแจ้งบุ๋ย ห้ามฝืนทำ
- กฎความปลอดภัยบัญชี, no-paid policy, experiment cap และข้อห้าม republish มีอำนาจเหนือ automation เสมอ

### เลน price experiment (บุ๋ยอนุมัติ 11 ก.ค.)
- เกณฑ์เสนอ (proposer, deterministic): LIVE + KENP ≥50 (มีคนอ่าน KU จริง) + royalties ≤$1 + ราคาปัจจุบัน >$2.99 → ทดลอง $2.99, วัดผล 14 วัน (contribution delta)
- ราคาปัจจุบันอ่านจาก listing.price → fallback pricing-recommendation.json `recommended_price_usd`; ไม่มีทั้งคู่ = ข้าม
- **ห้ามคร่อมโปรโม**: มีโปรโมในหน้าต่างวัดผล 14 วัน = เลื่อน (one variable per window) + gate executor refuse ถ้าโปรโมคลุมวันนี้ (KDP ล็อก royalty 35% ระหว่างโปรโม — set_price มี abort gate ชั้นสอง)
- Band $2.99-9.99 (70%) เท่านั้น, ≤1 mutation/รอบ เหมือนเดิม
