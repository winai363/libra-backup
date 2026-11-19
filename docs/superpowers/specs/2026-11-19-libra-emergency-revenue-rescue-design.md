# Libra Emergency Revenue Rescue — แบบสำหรับตรวจรับ

สถานะ: บุ๋ยอนุมัติแนวทางฉุกเฉินในแชทวันที่ 19 พ.ย. 2026; เอกสารนี้ล็อกขอบเขตก่อนแก้ production

## เป้าหมายและหลักฐานตั้งต้น

- เป้าธุรกิจเดิมคงไว้: KDP royalties มากกว่า USD 20 ในเดือน พ.ย. และ ธ.ค. 2026
- Snapshot ล่าสุด 18 พ.ย.: USD 0.72, 0 orders, 127 KENP; ขาด USD 19.28
- Search Console สด 30 ต.ค.–16 พ.ย.: 1 impression, 0 click
- เส้นทาง `search-surface`: 15 qualified requests ทั้งหมดในวันปล่อย 30 ต.ค. และ 0 Amazon outbound; requests ไม่ใช่คนและอาจปน release QA
- URL Inspection: หน้ารวม guides indexed; หน้าหัวข้อ TDAH และบทความตัวอย่างเป็น `Crawled - currently not indexed`; fetch, robots และ canonical ผ่าน
- รายได้ พ.ย. มาจาก `ai-productivity-workflows-professionals` ไม่ใช่ TDAH workbook ที่ Search-first โปรโมต

## ขอบเขตที่อนุมัติ

ทำสามช่วงตามลำดับ ห้ามเปิดโฆษณาก่อนช่วง 1–2 ผ่าน:

1. แก้ observability ของ Search-first โดยไม่เปลี่ยน KDP, ราคา, metadata, Pinterest หรือซื้อสื่อ
2. QA ความพร้อมขายของ TDAH Workbook ES, Beginner Watercolor ES และ AI Productivity Workflows; Senior Smartphone FR คง `HOLD_PROMOTION`
3. หากมีอย่างน้อยหนึ่ง ASIN ผ่าน QA และบัญชี Amazon Ads มีหลักฐานครบ ให้จัดทำ mutation plan และเปิด Sponsored Products แบบจำกัดความเสียหายตาม gate ด้านล่าง

TOTAL KDP FREEZE ถูกยกเว้นเฉพาะ Amazon Sponsored Products pilot นี้เท่านั้น ไม่อนุญาตให้แก้ราคา, metadata, cover, manuscript, category, appeal, unpublish, หนังสือใหม่, Pinterest, LinkedIn routine, email outreach หรือ paid channel อื่น

## ช่วง 1 — Rolling Search Console monitoring

### พฤติกรรมที่ต้องได้

- คง Day-7 checkpoint เดิมแบบส่งครั้งเดียวและไม่ส่งซ้ำ
- หลัง Day-7 จบ ระบบยังเก็บ Search Console ต่อ ไม่หยุดถาวรที่ `monitor_complete`
- เก็บ rolling 28-day final-data window สัปดาห์ละครั้ง; วันที่ข้อมูลยังไม่ final ให้รอโดยไม่บันทึกศูนย์แทน UNKNOWN
- Snapshot ทุกครั้งมี window, clicks, impressions, CTR, position, qualified requests, Amazon outbound, KDP latest และ freshness
- รายงานทั่วไปอ่าน Search Console snapshot ล่าสุดได้ พร้อมอายุข้อมูล; ถ้า stale หรืออ่านไม่ได้ให้แสดง UNKNOWN
- แจ้งเจ้าของเฉพาะรายงานรายสัปดาห์หรือ incident จริง ไม่ส่ง Telegram รายวัน

### วิธีทดสอบ

- Regression test ต้องพิสูจน์ว่า state แบบปัจจุบัน (`checkpoint=SENT`, `metrics=FINAL`) ยังถึงรอบเก็บ weekly metrics เมื่อครบ 7 วัน
- ก่อนครบ 7 วันต้อง NOOP โดยไม่เรียก provider
- Day-7 message เดิมยัง idempotent
- Provider error ไม่ทำลาย snapshot/state เดิมและไม่สร้างค่าศูนย์ปลอม
- Focused tests ผ่านก่อน full Libra suite

## ช่วง 2 — Product retail-readiness QA

ตรวจแบบอ่านอย่างเดียวและให้ผล `PASS`, `HOLD` หรือ `UNKNOWN` ต่อ ASIN:

- สถานะ LIVE, marketplace, ราคา และ landing URL
- promise-to-content: title/subtitle/cover/A+ ตรงกับไฟล์ที่ลูกค้าได้รับ
- ภาษา, medical/compliance claims, rights และข้อบกพร่องที่ทำให้ลูกค้าผิดหวัง
- ประวัติ royalties/orders/KENP รายเดือนและเหตุผลเลือกเข้าทดสอบ
- Amazon detail-page availability, review/rating evidence ถ้าเข้าถึงได้ และ policy eligibility

ลำดับผู้สมัคร:

1. `adhd-adults-workbook-es` — ตัวแรกเพราะเคยทำ USD 10.41 ใน ก.ย. และ Search-first มีสินทรัพย์รองรับ
2. `beginner-watercolor-spanish` — สำรองเมื่อ QA ภาพ/สิทธิ์/เนื้อหาผ่าน
3. `ai-productivity-workflows-professionals` — สำรองเมื่อ QA ความทันสมัยและความต่างของข้อเสนอผ่าน; activity พ.ย. อย่างเดียวไม่พออนุมัติ
4. `senior-smartphone-french` — HOLD จนกว่าหลักฐานภาพประกอบในไฟล์จริงตรงกับคำสัญญา

## ช่วง 3 — Amazon Sponsored Products pilot

### Mutation gate

เปิดจริงได้ต่อเมื่อมีหลักฐานครบทุกข้อ:

- บัญชี/profile/marketplace/region/currency/timezone และสิทธิ์เขียนชัดเจน
- operation สำหรับสร้าง/เปิด Sponsored Products ถูกทดสอบและมี adapter หรือ browser session ที่ยืนยันได้
- ASIN ผ่าน QA และ eligible
- exact before/after mutation plan ระบุ campaign, ASIN, targeting, bid, budget, dates, conversion, audit และ rollback
- remote state ไม่เปลี่ยนจาก snapshot ตอนอนุมัติ
- ไม่มี credential, cookie หรือ token ถูกเขียนลง repo/log

ถ้าข้อใดขาด ให้หยุดที่ draft; ห้ามอ้างว่าเปิดโฆษณาแล้ว

### โครงทดลอง

- Marketplace เริ่มต้น: Amazon.com / USD เฉพาะเมื่อ account evidence ยืนยัน; ไม่เดา region
- Product: หนึ่ง ASIN ต่อครั้ง เริ่ม TDAH Workbook ES หากผ่าน QA
- Objective: attributed paid book order และ royalties/contribution หลัง ad spend; ไม่ใช้ impressions/clicks เป็นความสำเร็จ
- Targeting: keyword exact/phrase ที่ตรงกับเนื้อหาเท่านั้น; ไม่สร้าง negative keywords ก่อนมี search-term evidence
- ระยะ: ไม่เกิน 14 วัน แต่ hard stop เมื่อถึงเพดานก่อน
- งบขาดทุนรวมสูงสุด: USD 10 สำหรับ pilot ทั้งหมด ไม่ใช่ต่อ campaign/ASIN
- Daily budget เริ่มต้นไม่เกิน USD 2; budget ของ platform ไม่ถือเป็น hard cap จึงต้องมี spend watcher/readback ก่อน apply
- ห้ามแก้ listing พร้อมกับ pilot เพื่อให้แยกผลได้

### Stop rules

หยุดแบบ reversible (pause, ไม่ delete) เมื่อเกิดข้อใดก่อน:

- spend รวมที่ verify แล้วถึง USD 10
- retail eligibility, listing availability, policy หรือ measurement ผิดปกติ
- provider state อ่านกลับไม่ได้หรือยอด spend freshness เกิน 24 ชั่วโมง
- มี click/spend แต่ conversion evidence ไม่ครบจนประเมินความเสียหายไม่ได้
- เจ้าของกดหยุด

ยังไม่กำหนด universal click-count/ACOS pause rule ก่อนเห็น bid, CPC, royalty per sale, attribution window และ search-term report จริง เพราะจะเป็นเกณฑ์ที่เดา

### Readout

- รายงาน side-by-side: Amazon attributed orders/sales/spend กับ KDP royalties/KENP; ห้ามบวกหรืออ้าง causal attribution เกินข้อมูล
- ผล `KEEP`, `ITERATE`, `STOP` เมื่อ attribution window ของบัญชีปิดและข้อมูล final
- การขยายงบต้องขออนุมัติใหม่; approval ฉบับนี้ไม่อนุญาตเกิน USD 10

## Rollback และความเสี่ยง

- Monitoring rollback เฉพาะ commit/release ของงานนี้ โดยรักษา state/snapshots เดิม
- Ads rollback = pause campaign/ad group/target ที่สร้างใน pilot และอ่านกลับยืนยัน; ไม่ลบถาวร
- ความเสี่ยงหลัก: organic indexing ช้า, Amazon account/adapter ไม่มี, QA ไม่ผ่าน, attribution lag และ ad spend ไม่สร้างยอด
- ความสำเร็จทางเทคนิคไม่เท่ากับเป้ารายได้; หาก พ.ย.ไม่เกิน USD 20 ให้บันทึก `TARGET_NOT_MET` ตามกฎเดิม ห้ามเลื่อนคู่เดือนไป ธ.ค./ม.ค. เงียบ ๆ

## เกณฑ์เสร็จงาน

- Rolling monitor ทำงานตามเทสต์และ production readback พร้อม snapshot สด
- QA มีหลักฐานและคำตัดสินครบสามผู้สมัคร โดย Senior Smartphone ยัง HOLD
- Amazon pilot ถูกเปิดและอ่านกลับได้ภายใต้ gate หรือมีรายงาน `BLOCKED_WITH_EVIDENCE` ที่ระบุ input/authority ที่ขาด
- memory.md และ handoff อัปเดต; commit เฉพาะไฟล์งานนี้และ push `backup main`
