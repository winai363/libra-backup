# Libra Public Guides — Calm Liquid Reader design

สถานะ: ผู้ใช้อនុมัติแนวทาง Calm Liquid Reader และสั่ง “ลุย” วันที่ 30 ต.ค. 2026

## เป้าหมาย

ออกแบบ public acquisition surfaces ทั้งสามระดับใหม่ให้สะดุดตา อ่านง่าย และมีภาษาภาพแบบ iOS 27 โดยไม่เลียนแบบหน้าจอระบบหรือทำกระจกพร่าทั้งหน้า:

1. `/libra/guides/` — หน้ารวมและทางเข้าจาก search
2. `/libra/guides/{topic}` — หน้ารวมหัวข้อและบทความ
3. `/libra/growth/articles/{article}` — หน้าบทความและทางไป Amazon

หน้ามีงานเดียวตามลำดับ: ทำให้คนเข้าใจหัวข้อ → เลือกบทความ → อ่านคำตอบที่ใช้ได้ → เห็นหนังสือที่เกี่ยวข้องและตัดสินใจกด Amazon. การออกแบบต้องผ่าน WCAG 2.1 AA และข้อกำหนด touch target ภายในอย่างน้อย 44×44 CSS px.

## หลักฐานและปัญหาของรุ่นปัจจุบัน

- Hero ภาษาอังกฤษขนาดใหญ่กินพื้นที่ แต่ไม่สื่อสิ่งที่ผู้อ่านสเปนจะได้ทันที
- หน้าแรกมีหัวข้อเดียวแต่ใช้กล่องว่างสูงเหมือน template; ไม่มีปกหนังสือ จำนวนบทความ หรือภาพจำของสินค้า
- Typography แบบ Georgia/Verdana และเส้นเหลืองบนกล่องให้ความรู้สึกเว็บบทความทั่วไป ไม่ใช่ภาษาภาพ Apple รุ่นปัจจุบัน
- หัวข้อหนึ่งมีลิงก์ซ้อนเชิงพื้นที่: pseudo-element ของลิงก์หัวข้อครอบทั้งการ์ด ขณะที่มีลิงก์ “Open the guide” อีกอันในกล่องเดียวกัน ทำให้ hit area และ focus behavior กำกวม
- ป้ายภาษาและเนื้อหาภาษาสเปนไม่ได้ระบุ language-of-parts บนครบทุก surface
- ลิงก์ข้อความและ CTA บางจุดมีพื้นที่กดต่ำกว่าเกณฑ์ภายใน 44px; ไม่มี skip link; alt ของปกในหน้าหัวข้อว่าง
- ไม่มี fallback เฉพาะสำหรับ Reduced Transparency, Increased Contrast และ Reduced Motion

## ทิศทางภาพ: Calm Liquid Reader

ยึดแนวทาง Apple ว่า Liquid Glass เป็น functional layer สำหรับ navigation และ controls ที่ลอยเหนือ content; ไม่ใช้ glass กับรายการบทความหรือพื้นเนื้อหา และไม่วาง glass-on-glass. เนื้อหาใช้พื้นทึบที่อ่านได้เสมอ ส่วนกระจกใช้สามตำแหน่งเท่านั้น:

- floating top navigation
- primary action capsule
- book action dock บนหน้า topic/article

บุคลิกเฉพาะของ Libra คือ **Focus Lens**: แสง cobalt–aqua รูปวงรีซ้อนด้านหลังปกและหัวเรื่อง สื่อการเลือก “สิ่งเดียวถัดไป” สำหรับผู้อ่าน TDAH. เป็น decoration ที่ `aria-hidden`, ไม่ต้องเห็นจึงเข้าใจหน้าได้ และหายเป็นพื้นทึบเมื่อผู้ใช้ลด transparency/เพิ่ม contrast.

### Design tokens

- `midnight #101828` — ข้อความหลัก
- `slate #344054` — ข้อความรอง
- `cloud #F7F8FC` — พื้นเนื้อหา
- `white #FFFFFF` — content surface
- `cobalt #155EEF` — primary action/focus; ใช้คู่ขาวเฉพาะเมื่อ contrast ผ่าน 4.5:1
- `indigo #3538CD` — accent เข้มและ focus support
- `aqua #0E9384` — accent/สถานะ ไม่ใช้เป็นข้อความเล็กบนพื้นสว่างจนกว่าจะผ่าน contrast
- `lavender #E4E7FF` และ `ice #DFF7F4` — background light fields เท่านั้น
- border แบบทึบ `rgba(16,24,40,.16)` พร้อม fallback สีทึบเมื่อเพิ่ม contrast

Typography ใช้ native system stack (`-apple-system`, `BlinkMacSystemFont`, `Segoe UI`, sans-serif) เพื่อให้ใกล้แพลตฟอร์มโดยไม่โหลด font ภายนอก. Display 700–760, body 400–500, utility 600; body ขั้นต่ำ 16px, line-height 1.6–1.75, reading measure ไม่เกิน 68ch. ห้ามใช้ serif ขนาดใหญ่เป็นเอกลักษณ์หลัก.

Radius ใช้ concentric family 32/24/16/999px. Shadow บางและมี border เสมอ จึงไม่พึ่งเงาเพื่อสื่อขอบเขต.

## โครงหน้า

### หน้ารวม

- `<html lang>` คำนวณจากหัวข้อที่เผยแพร่: ถ้าทุกหัวข้อภาษาเดียวใช้ภาษานั้น; ถ้าหลายภาษาใช้ `en` และกำกับ `lang` รายการ
- Spanish-first copy ใน release ปัจจุบัน: “Pequeños sistemas. Días más claros.” และคำอธิบายที่บอกว่ามีโน้ตปฏิบัติจริงกับหนังสือฉบับเต็ม
- top glass bar แสดง Libra Guides และจำนวนหัวข้อ/ภาษา ไม่ทำเป็นเมนูปลอม
- hero สองคอลัมน์บน desktop: ข้อความ + Focus Lens ที่มีปกหนังสือจริง; เรียงหนึ่งคอลัมน์บนมือถือ
- topic feature เป็น content surface เดียว มีภาษา จำนวนบทความ ชื่อ คำอธิบาย ปก และ CTA เดียว “Explorar 14 notas”. ไม่มีลิงก์ซ้อน
- editorial method ย้ายเป็น disclosure block ที่อ่านง่ายด้านล่าง ไม่แย่ง CTA

### หน้าหัวข้อ

- glass breadcrumb/nav สูงอย่างน้อย 48px
- heading และ summary ภาษาสเปน; แสดงจำนวนบทความเป็นข้อมูลจริง
- รายการบทความเป็นการ์ดทึบ กดได้ด้วยลิงก์เดียวต่อรายการ แต่ละรายการมีชื่อ คำอธิบาย และลูกศร decorative
- desktop ใช้สองคอลัมน์: article list + sticky book dock; mobile วาง book dock หลัง intro หรือท้ายรายการโดยไม่บังเนื้อหา
- ข้อความ English เดิม (“All guides”, “Practical notes”, “How this page is prepared”) แปลเป็น Spanish neutral สำหรับหัวข้อ ES

### หน้าบทความ

- glass breadcrumb กลับหัวข้อและปุ่มขนาดแตะได้
- article header มี topic label, H1 และคำอธิบาย; เนื้อหาอยู่บน white reading surface แยกจากพื้น ambient
- paragraph measure ≤68ch และ spacing ชัด; link ใน body มี underline
- disclosure ทางการแพทย์คงอยู่และเด่นด้วย icon/label ที่ไม่พึ่งสีอย่างเดียว
- book action dock ใช้ปก ชื่อ รูปแบบ/ราคา และ CTA ที่ติดตามเดิม; glass เป็นชั้น control รอบ action เท่านั้น เนื้อหาหนังสืออยู่พื้นทึบ
- “Sigue leyendo” เป็นรายการลิงก์การ์ดทึบ รักษา `?c=search-surface` ตามกฎ attribution เดิม

## Accessibility contract

- semantic landmarks: skip link → header/nav → main → complementary/footer ตามหน้าที่จริง
- heading hierarchy หนึ่ง H1 ต่อหน้า; H2/H3 ไม่ข้ามระดับ
- link/control ทุกตัวมี accessible name ที่อธิบายปลายทาง; ไม่มี nested/overlapping anchors
- touch target ≥44×44px; spacing ระหว่าง target ไม่ทำให้กดผิดง่าย
- normal text contrast ≥4.5:1, large text/UI/focus indicator ≥3:1; ไม่ใช้สีเพียงอย่างเดียวสื่อสถานะ
- `:focus-visible` หนาอย่างน้อย 3px มี offset และไม่ถูก clipping
- ปกหนังสือมี alt ตามชื่อเล่มเมื่อให้ข้อมูล; decoration ใช้ alt ว่าง/`aria-hidden`
- ระบุภาษาหน้าและ language-of-parts ตามจริง
- รองรับ keyboard ตาม DOM order; ไม่มี focus trap; Enter เปิด link ตาม native behavior
- `prefers-reduced-motion: reduce` ปิด transform/transition ที่ไม่จำเป็น
- `prefers-contrast: more` ใช้พื้นทึบ border ชัด และเลิก translucency
- `prefers-reduced-transparency: reduce` ใช้ fallback ทึบ; ถ้า browser ไม่รองรับ glass base ยังอ่านได้ด้วย background สีทึบก่อน `backdrop-filter`
- reflow ที่ 320px และ zoom 200% ไม่มี horizontal scroll ยกเว้นเนื้อหาที่จำเป็น (รุ่นนี้ไม่มี)
- requests จาก browser ตรวจรับใส่ `X-Libra-Measurement: synthetic` เพื่อไม่เพิ่ม qualified view

## สถาปัตยกรรมการแก้

- สร้าง stylesheet กลางหนึ่งไฟล์ใน `templates/` แล้ว inject ผ่าน placeholder เดียวเข้าสาม template เพื่อไม่ให้ token/fallback/accessibility แยกกันคนละรุ่น; ไม่เพิ่ม CDN หรือ third-party runtime
- app renderer ส่งข้อมูลที่มีอยู่แล้วเท่านั้น: cover URL, article count, language, title, description, format และ signed CTA. ไม่สร้างราคา/คำรับรอง/ผู้เชี่ยวชาญใหม่
- helper สร้าง topic/article cards ต้อง escape ทุก field และกำหนด `lang`/link เพียงจุดเดียว
- URL, canonical, sitemap, JSON-LD, search portfolio eligibility, signed redirect, campaign `search-surface`/`unattributed`, bot/synthetic filtering และ KDP/Pinterest policy ไม่เปลี่ยน
- หน้า held/QA_REQUIRED ยัง 404; Pinterest feed ยัง 404; TOTAL KDP FREEZE คงเดิม

## Motion และ interaction

- page load ใช้ opacity/translate เล็กน้อยเฉพาะ hero และ primary card ไม่ทำ stagger ทั้งหน้า
- hover ใช้ lift 2px + border tint; active ลด scale เล็กน้อยเฉพาะ pointer ที่รองรับ hover
- glass highlight เป็น static CSS layer; ไม่มี device-motion, canvas หรือ animation loop
- ทุก motion ปิดได้และไม่มีข้อมูล/ฟังก์ชันใดพึ่ง animation

## การตรวจรับ

Automated:

- route/regression tests เดิมทั้งหมดต้องผ่าน
- เพิ่ม tests สำหรับ shared theme injection, Spanish labels, single anchor per topic/article card, `lang`, meaningful cover alt, skip link, no duplicate link destination inside one card, canonical/JSON-LD/CTA attribution unchanged
- accessibility scan ด้วย axe-compatible runner บนทั้งสามหน้า ต้องไม่มี Critical/Serious WCAG 2.1 AA findings
- ตรวจ contrast token แบบคำนวณ, target dimensions จาก computed styles และไม่มี horizontal overflow

Manual/browser evidence:

- screenshots ที่ 390×844, 1440×1000, 320px และ 200% zoom
- keyboard-only: skip link, nav, article links, CTA, related links; focus visible และ order ถูกต้อง
- emulation: reduced motion, more contrast, reduced transparency เมื่อ browser รองรับ
- อ่าน accessibility tree สำหรับชื่อ/role/level/landmarks; ระบุว่านี่เป็น semantic inspection ไม่ใช่การรับรองแทน VoiceOver บนอุปกรณ์จริง
- production HTTP: approved surfaces 200, held topic 404, Pinterest feed 404, sitemap inventory เดิมครบ
- synthetic checks ไม่เพิ่ม `search-surface` qualified views

## การปล่อยและ rollback

- snapshot เฉพาะ app/templates/tests ที่แตะก่อน deploy
- named-file commit และ push `backup main`; ไม่ stage runtime articles/state/HANDOFF ที่ค้างจากงานอื่น
- restart `libra.service`, ตรวจ public URL และภาพจริงก่อนปิดงาน
- rollback ด้วย commit/ไฟล์ของ redesign เท่านั้น; ห้าม reset worktree หรือย้อน Search-first release/config

## นอกขอบเขต

- ไม่เปลี่ยน KDP listing, manuscript, cover, metadata, price หรือ Amazon state
- ไม่เปิด Pinterest, paid ads, email, Search Console หรือบริการภายนอกใหม่
- ไม่เขียนบทความใหม่และไม่เปลี่ยน semantic claim ของบทความ
- ไม่อ้างว่า Liquid Glass หรือ WCAG audit ทำให้ Google ranking/ยอดขายเพิ่ม และไม่อ้าง VoiceOver ผ่านจนกว่าจะทดสอบบนอุปกรณ์จริง

## แหล่งอ้างอิงทิศทาง

- Apple Human Interface Guidelines — Materials: https://developer.apple.com/design/human-interface-guidelines/materials
- Apple WWDC25 — Meet Liquid Glass: https://developer.apple.com/videos/play/wwdc2025/219/
- Apple WWDC26 — Design resources and refreshed Liquid Glass: https://developer.apple.com/wwdc26/guides/design/
