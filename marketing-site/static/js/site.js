/* Drudge Pest Control — bilingual (EN / AR) marketing site.
   No build step, no dependencies. Strings live here; markup uses data-i18n. */
const I18N = {
  en: {
    dir: "ltr", langlabel: "العربية",
    // nav
    nav_services: "Services", nav_why: "Why Drudge", nav_sectors: "Industries",
    nav_process: "How It Works", nav_contact: "Contact", nav_quote: "Get a Quote",
    // hero
    hero_badge: "Licensed & Certified Pest Control",
    hero_title: "Pest control that protects your business and your reputation.",
    hero_sub: "Drudge delivers professional, safe and fully-documented pest management for restaurants, hotels, schools and facilities across Egypt — backed by certified technicians and digital monitoring.",
    hero_cta1: "Request a Free Inspection", hero_cta2: "Our Services",
    stat_sites: "Sites protected", stat_services: "Pest services",
    stat_response: "Emergency response", stat_reports: "Documented reports",
    // services
    services_kicker: "What We Do",
    services_title: "Complete pest control services",
    services_sub: "One trusted partner for every pest problem — residential, commercial and industrial.",
    s1_t: "General Pest Control", s1_d: "Comprehensive, year-round protection against crawling and flying pests.",
    s2_t: "Rodent Control", s2_d: "Rats & mice managed with bait stations, traps and exclusion proofing.",
    s3_t: "Termite Treatment", s3_d: "Protect your building's structure from costly termite damage.",
    s4_t: "Cockroach Treatment", s4_d: "Targeted gel baiting and residual treatments that keep kitchens clear.",
    s5_t: "Bed Bugs Treatment", s5_d: "Thorough, discreet treatments that eliminate bed bugs at every stage.",
    s6_t: "Fumigation", s6_d: "Whole-space fumigation for severe infestations and stored goods.",
    s7_t: "Mosquito Control", s7_d: "Larviciding and fogging programs that reclaim your outdoor spaces.",
    s8_t: "Service Contracts", s8_d: "Scheduled recurring visits that keep you protected and audit-ready.",
    // why
    why_kicker: "Why Drudge",
    why_title: "Protection you can prove",
    why_sub: "We don't just treat pests — we document everything, so you're always inspection-ready.",
    w1_t: "Certified Technicians", w1_d: "Trained, licensed professionals using approved, safe products.",
    w2_t: "Compliance Certificates", w2_d: "Service certificates for food-safety & HACCP audits, on demand.",
    w3_t: "Digital Device Monitoring", w3_d: "Bait stations and traps mapped and tracked for pest-activity trends.",
    w4_t: "Bilingual Reports", w4_d: "Detailed visit reports in Arabic and English after every service.",
    w5_t: "Scheduled Programs", w5_d: "Automated recurring contracts so no visit is ever missed.",
    w6_t: "Safe & Approved", w6_d: "Family- and food-safe chemicals applied by the book.",
    // sectors
    sectors_kicker: "Industries We Serve",
    sectors_title: "Tailored programs for every sector",
    sec1: "Restaurants & Cafés", sec2: "Hotels & Hospitality", sec3: "Schools & Education",
    sec4: "Supermarkets & Retail", sec5: "Food Facilities & Warehouses", sec6: "Offices & Homes",
    // process
    process_kicker: "How It Works",
    process_title: "A clear, four-step program",
    p1_t: "Inspect", p1_d: "We survey your site, identify risks and map every device.",
    p2_t: "Treat", p2_d: "We apply targeted, safe treatments suited to your pests.",
    p3_t: "Monitor", p3_d: "We track devices and pest activity between visits.",
    p4_t: "Certify", p4_d: "We issue a documented report and service certificate.",
    // cta band
    band_title: "Ready to make pests someone else's problem?",
    band_sub: "Get a free site inspection and a no-obligation quote today.",
    band_btn: "Get a Free Quote",
    // contact
    contact_kicker: "Get In Touch",
    contact_title: "Request your free inspection",
    contact_sub: "Tell us about your site and we'll get back to you within one business day.",
    f_name: "Full name", f_phone: "Phone", f_email: "Email",
    f_sector: "Type of site", f_msg: "How can we help?", f_send: "Send Request",
    f_thanks: "Thank you! Your request has been received — our team will contact you shortly.",
    c_whatsapp: "WhatsApp", c_email: "Email", c_address: "Address", c_hours: "Working hours",
    c_addr_val: "26th of July Corridor, Cairo, Egypt", c_hours_val: "Saturday – Thursday, 9:00 – 18:00",
    // footer
    foot_tag: "Professional pest control & hygiene management.",
    foot_services: "Services", foot_company: "Company", foot_about: "About",
    foot_contact: "Contact", foot_rights: "All rights reserved.",
    foot_powered: "Operations powered by FoxSystems",
  },
  ar: {
    dir: "rtl", langlabel: "English",
    nav_services: "خدماتنا", nav_why: "لماذا درَدج", nav_sectors: "القطاعات",
    nav_process: "آلية العمل", nav_contact: "تواصل معنا", nav_quote: "اطلب عرض سعر",
    hero_badge: "مكافحة آفات مرخّصة ومعتمدة",
    hero_title: "مكافحة آفات تحمي عملك وسمعتك.",
    hero_sub: "تقدّم درَدج إدارة آفات احترافية وآمنة وموثّقة بالكامل للمطاعم والفنادق والمدارس والمنشآت في جميع أنحاء مصر — بدعم من فنيين معتمدين ومراقبة رقمية.",
    hero_cta1: "اطلب معاينة مجانية", hero_cta2: "خدماتنا",
    stat_sites: "موقع محمي", stat_services: "خدمة مكافحة",
    stat_response: "استجابة طوارئ", stat_reports: "تقارير موثّقة",
    services_kicker: "ماذا نقدّم",
    services_title: "خدمات مكافحة آفات متكاملة",
    services_sub: "شريك واحد موثوق لكل مشكلة آفات — سكنية وتجارية وصناعية.",
    s1_t: "مكافحة الآفات العامة", s1_d: "حماية شاملة على مدار العام من الآفات الزاحفة والطائرة.",
    s2_t: "مكافحة القوارض", s2_d: "السيطرة على الفئران والجرذان بمحطات الطُعم والمصائد والعزل.",
    s3_t: "مكافحة النمل الأبيض", s3_d: "احمِ هيكل مبناك من أضرار النمل الأبيض المكلفة.",
    s4_t: "مكافحة الصراصير", s4_d: "طُعوم جل ومعالجات متبقية تُبقي مطابخك نظيفة.",
    s5_t: "مكافحة بق الفراش", s5_d: "معالجات دقيقة وسرّية تقضي على بق الفراش في كل مرحلة.",
    s6_t: "التبخير", s6_d: "تبخير شامل للمساحات للإصابات الشديدة والبضائع المخزّنة.",
    s7_t: "مكافحة البعوض", s7_d: "برامج مكافحة اليرقات والتضبيب لاستعادة مساحاتك الخارجية.",
    s8_t: "عقود الخدمة", s8_d: "زيارات دورية مجدولة تبقيك محمياً وجاهزاً للتدقيق.",
    why_kicker: "لماذا درَدج",
    why_title: "حماية يمكنك إثباتها",
    why_sub: "نحن لا نكافح الآفات فحسب — بل نوثّق كل شيء لتكون دائماً جاهزاً للتفتيش.",
    w1_t: "فنيون معتمدون", w1_d: "محترفون مدرّبون ومرخّصون يستخدمون منتجات آمنة معتمدة.",
    w2_t: "شهادات امتثال", w2_d: "شهادات خدمة لتدقيقات سلامة الغذاء و HACCP عند الطلب.",
    w3_t: "مراقبة رقمية للأجهزة", w3_d: "محطات الطُعم والمصائد مُخرَّطة ومتابَعة لرصد اتجاهات نشاط الآفات.",
    w4_t: "تقارير ثنائية اللغة", w4_d: "تقارير زيارة مفصّلة بالعربية والإنجليزية بعد كل خدمة.",
    w5_t: "برامج مجدولة", w5_d: "عقود دورية تلقائية حتى لا تفوت أي زيارة.",
    w6_t: "آمن ومعتمد", w6_d: "مبيدات آمنة على العائلة والغذاء تُطبَّق وفق الأصول.",
    sectors_kicker: "القطاعات التي نخدمها",
    sectors_title: "برامج مصمّمة لكل قطاع",
    sec1: "المطاعم والمقاهي", sec2: "الفنادق والضيافة", sec3: "المدارس والتعليم",
    sec4: "محلات السوبرماركت والتجزئة", sec5: "المنشآت الغذائية والمستودعات", sec6: "المكاتب والمنازل",
    process_kicker: "آلية العمل",
    process_title: "برنامج واضح من أربع خطوات",
    p1_t: "المعاينة", p1_d: "نعاين موقعك ونحدّد المخاطر ونخرّط كل جهاز.",
    p2_t: "المعالجة", p2_d: "نطبّق معالجات آمنة ومستهدفة تناسب آفاتك.",
    p3_t: "المراقبة", p3_d: "نتابع الأجهزة ونشاط الآفات بين الزيارات.",
    p4_t: "الشهادة", p4_d: "نصدر تقريراً موثّقاً وشهادة خدمة.",
    band_title: "جاهز لتجعل الآفات مشكلة شخص آخر؟",
    band_sub: "احصل على معاينة مجانية للموقع وعرض سعر دون أي التزام اليوم.",
    band_btn: "احصل على عرض سعر مجاني",
    contact_kicker: "تواصل معنا",
    contact_title: "اطلب معاينتك المجانية",
    contact_sub: "أخبرنا عن موقعك وسنعاود الاتصال بك خلال يوم عمل واحد.",
    f_name: "الاسم الكامل", f_phone: "الهاتف", f_email: "البريد الإلكتروني",
    f_sector: "نوع الموقع", f_msg: "كيف يمكننا مساعدتك؟", f_send: "إرسال الطلب",
    f_thanks: "شكراً لك! تم استلام طلبك — سيتواصل معك فريقنا قريباً.",
    c_whatsapp: "واتساب", c_email: "البريد", c_address: "العنوان", c_hours: "ساعات العمل",
    c_addr_val: "محور ٢٦ يوليو، القاهرة، مصر", c_hours_val: "السبت – الخميس، 9:00 – 18:00",
    foot_tag: "مكافحة آفات احترافية وإدارة نظافة.",
    foot_services: "الخدمات", foot_company: "الشركة", foot_about: "من نحن",
    foot_contact: "تواصل", foot_rights: "جميع الحقوق محفوظة.",
    foot_powered: "العمليات مدعومة من FoxSystems",
  },
};

let LANG = localStorage.getItem("drudge_lang") || "en";

function applyLang() {
  const dict = I18N[LANG];
  document.documentElement.lang = LANG;
  document.documentElement.dir = dict.dir;
  document.querySelectorAll("[data-i18n]").forEach(el => {
    const v = dict[el.getAttribute("data-i18n")];
    if (v !== undefined) el.textContent = v;
  });
  document.querySelectorAll("[data-i18n-ph]").forEach(el => {
    const v = dict[el.getAttribute("data-i18n-ph")];
    if (v !== undefined) el.setAttribute("placeholder", v);
  });
  const lt = document.getElementById("lang-toggle");
  if (lt) lt.textContent = dict.langlabel;
}

function toggleLang() {
  LANG = LANG === "en" ? "ar" : "en";
  localStorage.setItem("drudge_lang", LANG);
  applyLang();
}

document.addEventListener("DOMContentLoaded", () => {
  applyLang();
  document.getElementById("lang-toggle").addEventListener("click", toggleLang);

  // mobile nav
  const burger = document.getElementById("burger");
  const links = document.getElementById("nav-links");
  burger.addEventListener("click", () => links.classList.toggle("open"));
  links.querySelectorAll("a").forEach(a =>
    a.addEventListener("click", () => links.classList.remove("open")));

  // contact form (front-end only — shows a confirmation)
  const form = document.getElementById("contact-form");
  form.addEventListener("submit", e => {
    e.preventDefault();
    form.reset();
    document.getElementById("form-thanks").classList.add("show");
  });

  // reveal on scroll
  const io = new IntersectionObserver(entries => {
    entries.forEach(en => { if (en.isIntersecting) en.target.classList.add("in"); });
  }, { threshold: 0.12 });
  document.querySelectorAll(".reveal").forEach(el => io.observe(el));

  // year
  document.getElementById("year").textContent = new Date().getFullYear();
});
