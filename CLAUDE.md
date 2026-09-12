# Libra — กฎถาวร (อ่านก่อนแตะ Libra/KDP ทุกครั้ง)

อ่าน `memory.md` (ท้ายไฟล์ = ล่าสุด) ก่อนเริ่มงานเสมอ. กฎที่ห้ามละเมิด:

## 12 ก.ย. 2026 (รอบห้า) — 🚦 ทางเข้าเดียวของการเปิดใช้ (ยังไม่ได้เปิด)
- เปิดใช้ทั้งหมดผ่าน `scripts/activate_organic_experiment.py` เท่านั้น (`preflight` → `day0` → `approve-next` → `record-publication` → `status`) · คู่มือ `docs/day0-activation-runbook-2026-09-12.md`
- `day0` ต้องมี `--confirm "PINTEREST VERIFIED — ACTIVATE LIBRA ORGANIC EXPERIMENT"` เป๊ะ ไม่ตรง = ไม่เขียนอะไรเลย · preflight ไม่ผ่าน = ไม่เปิดช่อง
- ⛔ `day0` **ไม่** เริ่มนาฬิกาการทดลอง — `active: true` เกิดที่ `record-publication` ซึ่งต้องมี url https + หลักฐานที่เปิดดูจริงเท่านั้น
- ⛔ อนุมัติได้ **1 บทความ/เลน/วัน** ตามลำดับ `publication_order` · เล่มไม่ LIVE = ไม่อนุมัติ · `published_at` ห้ามเป็นอนาคต (ฟีดจะทิ้งรายการนั้น)
- Pinterest RSS ต่อได้ 1 ฟีด = 1 บอร์ด ⇒ บอร์ด A (สเปน/TDAH) รับ Pin ทั้งหมด แล้วบุ๋ยย้าย 3 Pin ของเลน bilingual ไปบอร์ด B ด้วยมือ · แยกฟีดต่อ campaign = แก้โค้ด = ติด FREEZE
- ไม่ต้องเพิ่ม cron: รายงาน 09:55 เงียบอยู่จนมี publication แล้วรายงาน day 7/14/30 เอง

## 12 ก.ย. 2026 (รอบสี่) — ❄️ FREEZE ฟีเจอร์ของการทดลอง organic + คุณภาพภาษา
- ❄️ **ห้ามเพิ่มฟีเจอร์จนถึงการตัดสินวันที่ 30**: ห้ามช่องทางใหม่ · เล่มใหม่ · รูปแบบคอนเทนต์ใหม่ · แดชบอร์ดใหม่ · agent ใหม่ · ระบบ attribution ใหม่ · รื้อสถาปัตยกรรม — ยกเว้นของจริงพัง 5 อย่าง: การเผยแพร่ล้ม · tracking ล้ม · สถานะ KDP ของเล่มในทดลองเปลี่ยน · ปัญหา compliance · คอนเทนต์พังชัดเจน
- จังหวะตรวจ: วัน 0 = publication ที่ยืนยันแล้ว · วัน 7 = เช็คสุขภาพ/tracking เท่านั้น · วัน 14 = วินิจฉัย distribution ถ้าคลิกยัง 0 · วัน 30 = ตัดสิน · ⛔ ระหว่างจุดตรวจห้ามปรับจาก noise · ⛔ ห้ามแก้หนังสือเพราะทราฟฟิกน้อย
- ⛔ **ห้ามทำให้การรีวิวภาษาเป็นงานประจำของบุ๋ย** (บุ๋ยไม่ใช่เจ้าของภาษา ES/PT) — ใช้ระดับผล `SEMANTIC_QA_PASS` / `SEMANTIC_QA_UNCERTAIN` / `HUMAN_REQUIRED` และแก้ความไม่แน่ใจด้วยการเขียนใหม่เป็นภาษาที่กลางและง่ายกว่า · ⛔ ห้ามเขียนคำว่า "native verified" ถ้าไม่มีคนเจ้าของภาษาอ่านจริง
- ES = สเปนกลาง (ไม่มีคำเฉพาะประเทศ) · pt-BR = ภาษาที่คนบราซิลพูดจริง ไม่ใช่สำเนวนแปล · bilingual = ต้องใช้ได้ทั้งสองทิศทาง (ห้ามสมมติว่าสเปนเป็นภาษาเป้าหมายเสมอ)
- ตัว CTA/การขายอยู่บนการ์ดหนังสือ ไม่ต้องยัดย่อหน้าขายท้ายบทความ — บทความต้องให้คำตอบที่ใช้ได้ก่อนถึง CTA
- เอกสารพร้อมเปิดใช้: `docs/activation-readiness-2026-09-12.md` (พรีวิวให้บุ๋ยอนุมัติเป็นชุด + ขั้นตอน Pinterest/iCloud + ปฏิทิน 30 วัน)

## 12 ก.ย. 2026 (รอบสาม) — โหมดผู้ดำเนินการ organic: ช่องทาง คอนเทนต์ และประตูเล่มใหม่
- **1 เล่ม = 1 campaign** (`pin-adhd-es` · `pin-bilingual-kids` · `li-contab-pt`) และ `data/growth_campaigns.json` มี map `channels` (campaign → ช่องทาง) · ฟีด Pinterest เสิร์ฟเฉพาะ campaign ที่ map เป็น `pinterest-rss` ⇒ ⛔ บทความของเลน LinkedIn ห้ามกลายเป็น Pin
- campaign ในไฟล์บทความต้องเป็นชื่อที่ประกาศไว้ (ผ่าน `_hub_campaign` เหมือน `?c=`) · **บทความที่ `qa_approved` ไม่เป็น true = 404** (ไฟล์ที่วางในโฟลเดอร์ไม่ใช่การตัดสินใจเผยแพร่)
- ดราฟต์อยู่ `data/growth_articles_drafts/` (ไม่ถูก serve ไม่เข้าฟีด) · อนุมัติ = ตรวจตาม qa_checklist + แก้ทุกข้อใน `flagged_uncertainty` โดยเจ้าของภาษา → ตั้ง qa_approved/published_at → ย้ายเข้า `data/growth_articles` · ⛔ ตั้ง published_at ไล่วันละ 1 เล่ม (Pinterest ดันของเก่าก่อน ภายใน 24 ชม.) กัน Pin ถล่ม
- ⛔ ภาษาต่างประเทศต้องมีรีวิวเชิงความหมายจากเจ้าของภาษา ห้ามใช้การสแกนคำแทน · ห้ามเขียนคำโฆษณาทางการแพทย์ (ADHD) · ห้ามให้คำแนะนำภาษี (PT ต้องจบที่คนตรวจ) · ห้ามสัญญาผลการเรียน (bilingual)
- ช่องทางที่เลือกจากการให้คะแนน (intent × discoverability × fit × automation × compliance): Pinterest RSS = 2 เล่มผู้บริโภค · LinkedIn บุ๋ยโพสต์เอง = เล่ม PT · ⛔ Pinterest ไม่ใช่คำตอบสำหรับทุกเล่ม (เล่ม PT ได้ 13 คะแนน) · เหตุผลเต็ม `docs/organic-content-backlog-2026-09-12.md`
- รายงานคืน 4 คำตัดสินเท่านั้น: CONTINUE / ITERATE / INCONCLUSIVE / STOP-CHANNEL · ⛔ ไม่มีคำตัดสินใดปลดเล่มออกจากแคตตาล็อก · เทียบค่าลิขสิทธิ์แบบทิศทางเท่านั้น (up/down/flat/unknown)
- ⛔ **ประตูเล่มใหม่ปิดและบังคับในโค้ด**: `new_book_gate.py` + `data/new_book_gate.json` · `auto-generate.sh` exit 73 · `POST /api/books` ตอบ 423 · เปิดได้เมื่อประตู A (ดีมานด์จากเล่มเดิมพิสูจน์แล้ว) หรือ B (โอกาสแรงกว่าจากงานวิจัย) **และ** คอนเซปต์มีหลักฐาน 6 ข้อครบ (buyer_evidence · differentiated_promise · competitive_gap · acquisition_plan · compliance_review · expected_economics) · ⛔ ห้ามเพิ่ม env override · แยกจาก KDP freeze ซึ่งห้าม publish อยู่แล้วเสมอ

## 12 ก.ย. 2026 (รอบสอง) — ท่ออีเมลแจ้งเตือน + เลน Pinterest RSS + การวัดผลที่พิสูจน์ได้
- `scripts/mail_watch.py` อ่านได้หลายกล่อง: ทุกไฟล์ `*.env` ใน `/root/.config/mail-watch/` = 1 กล่อง (คีย์ `IMAP_HOST` รองรับ iCloud `imap.mail.me.com`) · กล่องเดิม (`imap.env` = Gmail) ยังใช้ state ไฟล์เดิม cursor ไม่รีเซ็ต · dedup ข้ามกล่องด้วย Message-ID (`data/mail-watch-seen.json`) เผื่อ forward ซ้ำ · `redact()` ลบรหัสผ่านออกจาก state/log/Telegram ทุกจุด
- ⛔ ยังไม่มีหลักฐานว่าเคยเห็นประกาศ KDP จริง — เทสต์ 20 ตัวเป็น **transport test กับ IMAP ปลอม** เท่านั้น. ต้องให้บุ๋ยทำอย่างใดอย่างหนึ่ง: สร้าง app-specific password ของ Apple ID ใส่ `icloud.env` (ดู `icloud.env.example`) หรือ ตั้ง rule ใน iCloud Mail ให้ forward เมลที่มี kdp/amazon ไป winai363@gmail.com
- `classify_status` แยก **`LIVE_UPDATES_IN_REVIEW`** ("Live - Updates in review" = ยังขายอยู่ แค่มีการแก้รออนุมัติ) ออกจาก `IN_REVIEW` · ตอบสนองตามสัดส่วน: IN_REVIEW/BLOCKED/UNPUBLISHED/DRAFT = พักแคมเปญเล่มนั้น · updates-in-review = เฝ้า ไม่พัก · ไม่อยู่ใน roster = hold ห้ามเดาว่า LIVE
- **เลน Pinterest RSS (ยังปิด)**: `growth_feed.py` + route `/growth/feed.xml` ปิดด้วย `posting_authorization.channel_authorized("pinterest-rss")` — ⛔ fail closed, `authorized:true` ไม่พอ ต้องมี `authorized_by` + `authorized_at`, ชื่อช่องทางนอก `KNOWN_CHANNELS` ไม่เคยผ่าน, ปิดอยู่ = 404. ⛔ ห้ามเพิ่ม env override / ห้ามเปิดช่องอื่นด้วยไฟล์นี้ (กฎ "ไม่โพสต์อัตโนมัติ" 30ส.ค. ยังมีผลทั้งหมด — ไฟล์นี้แค่เปิดช่องเดียวแบบระบุชื่อ)
- ฟีดรับเฉพาะบทความ `qa_approved: true` ใน `data/growth_articles/` · ลิงก์/รูปต้องอยู่ใต้ `/libra/growth/` และ `/libra/api/books/` · ห้ามมีคีย์ที่พาเนื้อหนังสือไปด้วย (`manuscript/ebook_md/sample_text/epub/pdf`) · เพดาน 5 รายการ (hard cap 20) กัน Pin ถล่ม · ดราฟต์เก็บที่ `data/growth_articles_drafts/` (ไม่ถูก serve)
- Pinterest ต้องมี **business account + claim โดเมน/ซับพาธ** (อ้างเอกสารทางการ 12ก.ย. ใน `docs/pinterest-rss-workflow.md`) → งานของบุ๋ย ห้าม login/ทำแทน
- รายงาน organic: หน้าต่างวัดผลเริ่มที่ **publication ที่พิสูจน์แล้ว** (url https + เวลาที่เห็น + หลักฐาน) เท่านั้น — ⛔ ไฟล์ RSS, cron, หรือโพสต์ที่เตรียมไว้ ไม่ใช่การเผยแพร่ · 25 คลิก = เกณฑ์ acquisition ชั่วคราว (reach) ⛔ ไม่ใช่การพิสูจน์ยอดขาย · คลิก 0 ที่วัน 14 = ไปตรวจ distribution/tracking ⛔ ห้ามปลดเล่ม · ห้ามอนุมานยอดซื้อจากคลิก และห้ามคำนวณ read-through จาก KENP
- คลิกทดสอบของเราเองลงทะเบียนใน `/root/shared/synthetic_events.json` (table `hub_events`, column `event_key`) แล้วรายงานหักออกด้วย `synthetic_events.sql_exclusion` — แถวดิบคงไว้เสมอ
- cron ใหม่ 09:55 `scripts/organic_experiment_report.py --send` (อ่านอย่างเดียว เงียบสนิทตอน experiment inactive) · rollback = ลบบรรทัด cron
- ⛔ ขายตรง (Payhip/Stripe) แยกออกจากเลน Amazon: **เก็บเงินได้ แต่พิสูจน์ไม่ได้** เพราะไม่มี `STRIPE_SECRET_KEY_LIVE` — ห้ามใส่คีย์แทนบุ๋ย ห้ามผ่อนการพิสูจน์ · เล่มเก่า 3 เล่มในทดลองนี้ยัง **Enrolled KDP Select** (ตามไฟล์ มิ.ย./ก.ค.) ⇒ ห้ามเอา EPUB ไปขายตรงจนอ่านสถานะจากหน้า KDP ใหม่ (KENP ย้อนหลังไม่ใช่หลักฐานสิทธิ์) ดู `docs/direct-sales-blockers-2026-09-12.md`

## 12 ก.ย. 2026 — การวัดผลคลิก + ด่านเฝ้า takedown (ไม่แตะ KDP)
- `/growth/out/{token}` ไม่บันทึก event ถ้า user agent เป็นบอท/ตัวดึงพรีวิว/สคริปต์ (`content_hub.is_bot_user_agent`) — ยัง redirect ปกติ. เบราว์เซอร์ในแอป (Pinterest/FB/IG/WhatsApp) นับเป็นคนจริง ห้ามใส่ `pinterest`/`whatsapp` แบบคำกว้างกลับเข้า marker
- `/growth/books/{slug}?c=<campaign>` ติดป้ายช่องทางได้ แต่ **เฉพาะชื่อที่ประกาศไว้ใน `data/growth_campaigns.json`** ชื่ออื่นตกกลับ `content-hub` (กันคนนอกเขียนป้ายใหม่ลงข้อมูลคลิกของเรา)
- `kdp_bookshelf_roster.py::compute_alerts` เพิ่ม `in_review` + `unpublished` — BLOCKED คือปลายทาง ทุกเล่มที่เคยโดนบล็อกผ่าน review มาก่อน จึงต้องเตือนตั้งแต่เห็น IN REVIEW (เทสต์ `tests/test_bookshelf_roster_alerts.py`)
- 🔴 **อีเมลแจ้งเตือนของ KDP เข้าบัญชี iCloud (KDP_EMAIL) ไม่ใช่ Gmail ที่ `mail_watch.py` เฝ้า** — สแกน INBOX ที่เฝ้าอยู่ย้อนถึง 1 มิ.ย. 2026 เจอเมลจาก amazon/kdp **0 ฉบับ** ⇒ ตอนนี้รู้เรื่องบล็อกได้ทางเดียวคือ roster scrape รายวัน 08:45. เติม `kdp` ใน `WATCH_SENDERS` แล้ว รอบุ๋ยตั้ง forward iCloud → winai363@gmail.com เอง (ห้ามไปแตะบัญชี iCloud)
- เลนทดลอง organic = `data/organic_experiment.json` (`active:false` = ยังไม่เริ่ม) + `scripts/organic_experiment_report.py` (อ่านอย่างเดียว ไม่ตัดสินใจ ไม่ตั้ง cron) + `docs/organic-experiment-2026-09-12.md`. ⛔ ห้ามแปลงตัวรายงานนี้เป็น agent ตัดสินใจ และห้ามผูกคลิกกับยอดขายเป็น conversion (Amazon ไม่ส่ง referrer)
- cron reconcile เปลี่ยนป้าย `--mode test` → `--mode live` ให้ตรง `LIBRA_COMMERCE_MODE=live` (ป้ายในรายงานเท่านั้น ตัวกรองโหมดจริงอยู่ที่ webhook). ⚠️ ยังไม่มี `STRIPE_SECRET_KEY_LIVE` ⇒ ถ้ามีคนซื้อจริงผ่าน Payhip จะ **พิสูจน์ด้วย Stripe ไม่ได้** (กฎ "Payhip สังเกต / Stripe พิสูจน์") — บุ๋ยต้องใส่คีย์เอง

## 7 ก.ย. 2026 — ปิดช่องโหว่ด่านตรวจภายใน
- ผล editorial ต้องมี SHA-256 ของ ebook.md และ listing.json ตรงกับไฟล์ปัจจุบัน โดยจับค่าก่อนเรียก reviewer; ห้ามเติม hash ย้อนหลังให้ผลเก่าเพื่อทำให้ผ่าน
- Dashboard และ strategy API ต้องสะท้อน freeze_state(): ห้ามแสดงแผนกรกฎาคม ตารางส่งหนังสือ วันลองใหม่ หรือการทดลองที่ยกเลิกเป็นงานปัจจุบัน
- `quality_gate.py` ตรวจหลักฐาน editorial ซ้ำ ไม่เชื่อ `passed` เก่า: คะแนนต้องถูกชนิด/ครบ, `recommended_action=pass`, ไม่มี critical issues; สารคดีต้องมี supported checks อย่างน้อย 5 ข้อพร้อม URL ที่รูปแบบถูกต้อง. URL ถูกฟอร์แมตไม่ได้พิสูจน์ว่าแหล่งข้อมูลจริงหรือสนับสนุนข้ออ้าง
- คำสัญญาว่ามีภาพใน title/subtitle/description เปิดการตรวจ EPUB อัตโนมัติ. นิช visual ที่สั่ง `require_visuals=True` ยังต้องมีภาพพร้อม provenance 12 รูป; บทสาธิตที่ตรวจพบจากหัวข้อมีหมายเลขต้องมีภาพอยู่ภายในบทด้วย. ตัวตรวจคำหลายภาษายังไม่ครอบคลุมทุกคำสัญญา
- `catalogue_quality.py --output-dir /root/downloads/libra-audit-YYYY-MM-DD` ตรวจคลังแบบอ่านอย่างเดียว; เก็บ hash ไฟล์+ข้อบกพร่องรายเล่มแยกจากหนังสือ ไม่เขียนทับคะแนน/ต้นฉบับ/สถานะ KDP
- ผล structural checks ผ่านไม่ใช่ semantic review: ยังต้องตรวจภาษา ความถูกต้องภาพ สิทธิ์ และลองทำตามเนื้อหาจริง. ไม่มีผลใดปลด TOTAL KDP FREEZE

## 6 ก.ย. 2026 — รื้อระบบภายในตามคำสั่งบุ๋ย
- อนุญาตแก้การวัดผลและด่านตรวจไฟล์ภายใน; TOTAL KDP FREEZE และข้อห้ามเผยแพร่ด้านล่างยังมีผลครบ
- สาเหตุการบล็อกยังไม่ยืนยัน: AI disclosure/ประวัติบัญชีเป็นสมมติฐาน ไม่ใช่ข้อเท็จจริง; คะแนน editorial ผ่านไม่ได้พิสูจน์ว่าเนื้อหาผ่านมาตรฐาน Amazon
- หลักฐานใหม่จากไฟล์: senior-smartphone-french สัญญามีภาพ แต่ EPUB ไม่มีภาพเนื้อใน; aquarelle FR มี 12 ภาพจริงในไฟล์ แต่มีภาพในบทสาธิตเพียง 2 จาก 12 บท (ดู docs/libra-editorial-audit-2026-09-06.md)
- ตรวจภาพที่ถูกใช้งานใน EPUB ด้วย ไม่ใช่แค่ไฟล์ใน images/; จำนวนภาพผ่านยังต้องตรวจความตรงกับบทเรียนและคำสัญญาหน้าขาย
- ยอดรายเดือนใช้ค่าล่าสุดที่สังเกต ไม่ใช้ค่าสูงสุด; อันดับ LIVE ใช้รายได้เฉพาะ LIVE; ข้อมูล paid/free, กำไร และเหตุผลบล็อกที่ไม่ทราบต้องแสดง unknown

## ⛔⛔ TOTAL KDP FREEZE (2 ส.ค. 2026 — บุ๋ยสั่ง "ห้ามเกิดปัญหานี้") — กฎนี้อยู่เหนือทุกกฎด้านล่าง
Amazon บล็อก ebook อีก 2 เล่มวันเดียว (TDAH ES เล่ม1 B0H6TZNC4K + ADHS DE B0H6H2D17K) → บล็อกสะสม 4 เล่ม. **22 ส.ค. 2026 บล็อกเล่มที่ 5**: `aquarelle-botanique-debutants-fr` (submission 67406856), ข้อความ "might result in a disappointing customer experience". เล่ม ES ถูกบล็อกหลังเปลี่ยนราคา; DE หลังแก้เนื้อหา; FR หลังผ่านคะแนนภายในและใส่ภาพ 12 รูป. เหตุการณ์เหล่านี้ไม่พิสูจน์สาเหตุเฉพาะว่าเป็น AI disclosure หรือประวัติบัญชี และไม่พิสูจน์ว่าเนื้อหาถูกต้องครบ. ยังมีความเสี่ยงต่อบัญชี จึงคง TOTAL KDP FREEZE ตามคำสั่งบุ๋ย
1. ⛔ **ห้าม republish / เปลี่ยนราคา / แก้ metadata / อัปโหลดเนื้อหา** เล่มใดๆ บน KDP — ต่อให้บุ๋ยเคยอนุมัติหลักการไว้ ต้องเตือนความเสี่ยงบล็อก+ปิดบัญชีก่อนทุกครั้งและรอคำยืนยันใหม่
2. ⛔ ห้าม appeal / reply อีเมลบล็อก / resubmit เล่มที่โดนบล็อก (ทั้ง 5 เล่ม)
3. ⛔ ห้ามอัปโหลดหนังสือใหม่เข้า KDP บัญชีนี้ — เล่มใหม่ที่ผ่านด่านภายในถูกบล็อก 22 ส.ค.; ด่านภายในไม่ใช่การรับรองจาก Amazon ⇒ เลนเล่มใหม่บน KDP ปิดถาวร ห้ามเสนอเปิดใหม่
4. เลน "ปลอดภัย" ด้านล่าง (free promo / ราคา / A+) **ถูกระงับทั้งหมด** — ราคาก็พิสูจน์แล้วว่า trigger review ได้ (ES 2 ส.ค.)
5. โหมดปัจจุบัน = **PASSIVE MODE ถาวร**: เล่มที่เหลือ ~38 ขายเอง, งบ/แรง = 0, ห้าม unpublish, cron ที่เหลือ = read-only เท่านั้น (sales sync / bookshelf roster / session ensure / รายงาน). ก่อนเปิด cron ใดๆ คืน เช็ค memory `libra-blocked-adhd-books-20260802` ก่อน
6. แผน expert review 2 ส.ค. (ส.ค. ammo month / Gate 31 ส.ค. / October Play) **ยกเลิกทั้งหมด** — ห้ามติดตั้ง gate_20260831.py หรือ cron ใหม่ใดๆ ของแผนนั้น; Ebrolis + LovelyBooks push ยกเลิก

### 🔴 ผลการทดลองเล่มใหม่ 1 เล่ม (22 ส.ค. 2026) — **FAIL / บล็อกครั้งที่ 5 / ปิดเลนถาวร**
บุ๋ยอนุมัติอัปโหลด `aquarelle-botanique-debutants-fr` (สีน้ำพฤกษศาสตร์ FR, 73 หน้า, ภาพสาธิต 12 รูป, editorial 8/8, อ้างอิง 13 รายการ) เป็นข้อยกเว้นเล่มเดียวผ่าน `APPROVED_UPLOADS`
- อัปโหลดสำเร็จบ่าย 22 ส.ค. (kdp_book_id `A2HGRQ4KXYKLSA`, AI disclosure ครบ, 3 หมวด leaf ถูกต้อง) → **Amazon ตีตกเย็นวันเดียวกัน 19:52** (submission 67406856) ข้อความเดิม "disappointing customer experience"
- ⇒ การมีภาพและคะแนนภายในผ่านไม่พอจะสรุปว่าเนื้อหาไม่มีปัญหา; ยังไม่ทราบสาเหตุที่ Amazon บล็อก จึงคงข้อห้ามส่งซ้ำ
- สถานะปัจจุบัน: `APPROVED_UPLOADS = {}` (ว่าง), `queue.txt` ว่าง, cron `process_kdp_queue.sh` PAUSED ทั้ง 09:00/13:00 → freeze ปิดสนิท **ไม่มี auto-retry**
- listing `/root/kdp/aquarelle-botanique-debutants-fr/listing.json` → `live_status=BLOCKED` + `blocked{}` แล้ว
- ⛔ ห้าม appeal / ห้าม reply อีเมล / ห้าม resubmit / ห้ามลองนิชอื่น — และ **ห้ามเสนอทดลองเล่มใหม่บน KDP อีก** ต่อให้คุณภาพดีแค่ไหน
- ✅ ทางออกของเล่มนี้: ขายตรงผ่าน Lemon Squeezy/Payhip ได้ (ไม่ได้ enroll KDP Select จึงไม่ผิด exclusivity) — งานที่ทำไปไม่เสียเปล่า

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

## 🛒 เลนขายตรง Payhip + Stripe (**LIVE MODE** — บุ๋ยอนุมัติ 22 ส.ค. 2026)
- ⚠️ **Payhip ไม่มี sandbox** ทุกการซื้อคือเงินจริง → ระบบรันโหมด `live` (เดิม test)
- คีย์แยกตามโหมดเด็ดขาด: `*_TEST` / `*_LIVE` — คีย์ test ใช้แทน live ไม่ได้ และ event ที่ `livemode` ไม่ตรงโหมดถูกปฏิเสธ `wrong_mode` ทั้งสองทาง
- ⛔ **live secret key (`sk_live_…`) บุ๋ยต้องใส่ใน .env เองเท่านั้น ห้ามส่งผ่านแชท**
- ทดสอบด้วยโค้ดส่วนลด: `scripts/payhip_coupon.py --create CODE --percent-off 95 --product-key GDRi5` (Payhip API เป็น form-encoded + ต้องมี browser UA ไม่งั้น Cloudflare 403)
- สินค้าแรก: `payhip.com/b/GDRi5` €12.90 · หน้าขายเรา `/libra/growth/products/aquarelle-botanique-debutants-fr`

## 🛒 กฎเดิมของเลนนี้ (ยังใช้ได้)
- **Payhip สังเกต / Stripe พิสูจน์** — event จาก Payhip สร้างรายได้เองไม่ได้ ต้องมี Stripe verified ตรง id+จำนวน+สกุล
- ⛔ **ห้ามขาย EPUB ของเล่มที่อยู่ใน KDP Select** (`payhip_catalog.guard_book_for_payhip` บล็อกไว้) — เล่มเก่า 39/64 อยู่ใน Select ห้ามเอาไป Payhip; เฉพาะเล่มใหม่ที่ไม่ enroll เท่านั้น
- Payhip ไม่มี API สร้างสินค้า/webhook → ใช้ `payhip_admin.py` (Playwright) ต้องมี before/after evidence; ครั้งแรกรัน `scripts/payhip_publish.py --inspect` ยืนยัน SELECTORS ก่อน `--execute`
- readiness: `python3 scripts/commerce_setup_check.py` (+ `--stripe` สร้าง webhook endpoint ให้เอง); runbook เต็ม `docs/runbooks/libra-commerce-test-mode.md`
- ค่าที่ไม่รู้ = null ห้ามใส่ 0 · สกุลเงินไม่รวมกัน · payout ≠ รายได้ · `paid_spend_minor: 0` เสมอ
- ⚠️ `LIBRA_GROWTH_TRACKING_SECRET` ต้องอยู่ใน `.env` (app.py export ให้ service) — ก่อน 22 ส.ค. หน้า /growth/books/* คืน 503 ตลอด ลิงก์ hub ที่โพสต์ไปตายหมด

## 📊 ข้อเท็จจริงจากข้อมูลจริง (วัดเมื่อ 22 ส.ค. 2026 — ห้ามเดาแทน)

รัน `python3 demand_analysis.py` เพื่ออัปเดตตัวเลขก่อนเสนออะไรก็ตามเรื่องสินค้าใหม่ (read-only ทั้งหมด ไม่มี LLM ตัดสิน)

- รายได้ที่วัดได้ **$25.58** ตลอด 11 ก.ค.–21 ส.ค. (63 เล่มในระบบ / 38 LIVE) — **31 จาก 38 เล่ม LIVE ได้ $0**
- **hub_events = 0 แถว** ⇒ ไม่พบ event ที่บันทึกไว้ ไม่พิสูจน์ว่าไม่มีคนเห็นสินค้า หรือว่าระบบ tracking ใช้ได้ครบ. ห้ามสรุปว่านิชไหนไม่มี demand จากยอด $0 เพียงอย่างเดียว
- ธีมที่ทุ่มเล่มแล้วได้ศูนย์: **anxiety/mental-health 12 เล่ม = $0**, **ภาษี/บัญชีสเปน 8 เล่ม = $0**, **ai_productivity 20 เล่มได้รวม $5.51 ($0.50/เล่ม LIVE)**
- ธีมที่มีสัญญาณ (n เล็กมาก ทั้งหมด confidence=low): art_craft $3.19/เล่ม · adhd $3.10 · senior_tech $2.53 · kids_language $2.42
- **ข้อสรุปเก่าว่าทั้งคลังไม่มีภาพใช้ไม่ได้แล้ว**: aquarelle FR มีภาพเนื้อใน EPUB 12 รูป แต่บทสาธิตที่สัญญาไว้ยังมีภาพไม่ครบ. ต้องใช้ผลตรวจไฟล์รายเล่มล่าสุด; ห้ามใช้จำนวนภาพสรุปสาเหตุบล็อก
- KDP snapshot เป็นยอด**สะสมรายเดือน** — ห้ามบวกแถวรายวัน; ใช้ค่าล่าสุดที่สังเกตต่อเดือน เพื่อรับยอดปรับลดย้อนหลังด้วย
- ADHD ES และ acuarela ที่ทำเงินได้ **เป็นเล่มที่ถูกบล็อกไปแล้ว** — ห้ามนับเป็นเหตุผลรีไซเคิลนิชนั้นบนบัญชีนี้

## 🚫 ห้าม republish เล่มที่เคย publish แล้ว (มี ASIN)
ทุกการ republish (เปลี่ยนหมวด/subtitle/description/ปก/เนื้อใน) = ส่งเล่มกลับเข้า Amazon content review ใหม่ = ทอยลูกเต๋า. **acuarela ถูก reject "disappointing customer experience" + หลุดจากร้าน (404) เมื่อ 11 ก.ค. 2026 จากการ republish เพื่อเปลี่ยนหมวดเท่านั้น.** ปกเคยผ่าน (3 ก.ค.) ≠ การันตีว่าจะผ่านอีก.
- Gate ในโค้ด: `scripts/kdp_action_executor.py::validate_action` refuse `category_update` ทุก listing ที่มี `asin` — ห้ามถอย gate นี้
- งานที่พับเก็บเพราะกฎนี้ (11 ก.ค.): subtitle rewrite 12 เล่ม, cover regen 39 เล่ม — ห้ามหยิบมาทำโดยไม่มีคำสั่งบุ๋ยชัดๆ

## 🔴 บัญชี KDP มี content block สะสม 5 ครั้งที่บันทึกไว้ — ปกป้องบัญชีมาก่อนทุกเล่ม
1. `high-protein-meal-plan-french` (นิช diet — NO-GO gate มีแล้ว)
2. `acuarela-para-principiantes-guia-paso-a-paso` (11 ก.ค. 2026 — บุ๋ยตัดสิน: ปล่อยตาย **ห้าม appeal / ห้าม resubmit**)
3. `adhd-self-help-adults-es` เล่ม 1 (2 ส.ค. 2026 — โดนจากการเปลี่ยนราคา; paperback ยัง LIVE)
4. `adhd-workbook-german-adults` (2 ส.ค. 2026 — โดนทั้งที่เนื้อหาแก้แล้ว)
5. `aquarelle-botanique-debutants-fr` (22 ส.ค. 2026 — ดูรายละเอียดด้านบน; สาเหตุเฉพาะยังไม่ทราบ)

block ครั้งถัดไปเสี่ยงระดับปิดบัญชี. ห้ามทำอะไรที่เพิ่มโอกาสโดน review โดยไม่จำเป็น.

## กฎเลนเก่า — ระงับทั้งหมดโดย TOTAL KDP FREEZE ด้านบน
- **Free promo / Countdown** — หน้า promotion-manager (`scripts/free_promo_auto.py`; โหมด manual: `--only <slug> --force --start YYYY-MM-DD --days N`, dry-run ก่อนเสมอ)
  - **กฎ pairing (14 ก.ค. 2026):** free promo ต้องมีคู่ช่องทางขยาย traffic เสมอ — ประกาศใน `data/promo_pairings.json` หรือมีคิวโพสต์ใน `data/reddit_promo_schedule.json` (executor refuse ถ้าไม่มี) เหตุผล: วัดจริง 17 โปรโม → 13 เล่มที่แจกเดี่ยวๆ ได้ 0 downloads และโควตาฟรีมีแค่ 5 วัน/เล่ม/เทอม เมื่อ executor schedule โปรโมสำเร็จจะเติมคิวเตือน Reddit ให้อัตโนมัติ
  - **ก่อนเสนอ/นัด promo เล่มไหน เช็คหน้า promotion-manager จริงก่อนเชื่อ listing.json** — เคยเจอเล่มที่โปรโมไปแล้ว 4-6 ก.ค. แต่ listing ไม่มีบันทึก (ai-creative-workbook-italian) ทำให้เกือบแจกซ้ำ
- **ราคา** — หน้า pricing ตรง (`scripts/set_price.py` มี SAFETY GATE royalty 35% ค้างระหว่างโปรโม — ห้าม publish ถ้า gate abort)
- **A+ Content** — ระบบแยก ไม่แตะตัวเล่ม
- **Ads / external traffic** — ไม่แตะตัวเล่ม (แต่ ads ติดเงื่อนไข checkpoint 31 ก.ค. + เพดานงบ)

## 📕 กฎเนื้อหา
- นิช visual (สอนวาด/ทำอาหาร/งานฝีมือ/คู่มือมีภาพประกอบ) **ต้องมีภาพสาธิตจริงในเนื้อ** ตามคำสัญญาที่ให้ผู้อ่าน; ไฟล์ไม่มีภาพคือข้อบกพร่องที่ตรวจได้ ไม่ใช่หลักฐานยืนยันสาเหตุเฉพาะที่ Amazon บล็อก
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
