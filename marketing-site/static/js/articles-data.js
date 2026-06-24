/* Article registry. Full bodies live in article-<id>.js (loaded on the reader). */
window.ARTICLES = window.ARTICLES || {};
window.ARTICLE_LIST = [
  {
    id: "ipm", icon: "🛡️",
    en: { tag: "Strategy", title: "Integrated Pest Management (IPM): The Complete Guide for Businesses",
      excerpt: "Why spraying alone never works — and how a structured Inspect → Identify → Prevent → Treat → Monitor → Document cycle keeps premises pest-free for good.", read: "8 min read" },
    ar: { tag: "استراتيجية", title: "الإدارة المتكاملة للآفات (IPM): الدليل الكامل للمنشآت",
      excerpt: "لماذا لا يكفي الرش وحده — وكيف تحافظ دورة منظّمة من المعاينة والتحديد والوقاية والمعالجة والمراقبة والتوثيق على منشأتك خالية من الآفات.", read: "قراءة ٨ دقائق" },
  },
  {
    id: "cockroach", icon: "🪳",
    en: { tag: "Commercial", title: "Cockroach Control in Commercial Kitchens: A Practical Playbook",
      excerpt: "Identification, sanitation, gel baiting science and monitoring — the field-tested steps that clear roaches from kitchens and keep them out.", read: "8 min read" },
    ar: { tag: "تجاري", title: "مكافحة الصراصير في المطابخ التجارية: دليل عملي",
      excerpt: "التعرّف على الأنواع، والنظافة، وعلم الطُعوم الهلامية، والمراقبة — خطوات مُختبَرة ميدانياً لإخلاء المطابخ من الصراصير ومنع عودتها.", read: "قراءة ٨ دقائق" },
  },
  {
    id: "rodent", icon: "🐭",
    en: { tag: "Facilities", title: "Rodent Control & Prevention for Facilities and Warehouses",
      excerpt: "Rats and mice cost businesses through contamination, damage and fire risk. Learn exclusion, sanitation, trapping and tamper-resistant baiting that actually works.", read: "8 min read" },
    ar: { tag: "منشآت", title: "مكافحة القوارض والوقاية منها في المنشآت والمستودعات",
      excerpt: "تكلّف الفئران والجرذان المنشآت عبر التلوث والأضرار وخطر الحرائق. تعرّف على العزل والنظافة والمصائد ومحطات الطُعم الآمنة الفعّالة.", read: "قراءة ٨ دقائق" },
  },
  {
    id: "bedbugs", icon: "🛏️",
    en: { tag: "Hospitality", title: "Bed Bugs: Detection, Treatment and Prevention",
      excerpt: "Bed bugs travel fast and hide well. A complete guide to spotting them early, treating them properly, and protecting hotels and homes from re-infestation.", read: "9 min read" },
    ar: { tag: "ضيافة", title: "بق الفراش: الكشف والمعالجة والوقاية",
      excerpt: "ينتقل بق الفراش بسرعة ويختبئ جيداً. دليل كامل لاكتشافه مبكراً ومعالجته بشكل صحيح وحماية الفنادق والمنازل من عودة الإصابة.", read: "قراءة ٩ دقائق" },
  },
  {
    id: "foodsafety", icon: "📄",
    en: { tag: "Compliance", title: "Pest Control & Food-Safety Compliance (HACCP)",
      excerpt: "What auditors expect from your pest program — the documentation, device monitoring and certificates that turn pest control into proof of compliance.", read: "9 min read" },
    ar: { tag: "امتثال", title: "مكافحة الآفات والامتثال لسلامة الغذاء (HACCP)",
      excerpt: "ما الذي يتوقّعه المدقّقون من برنامج مكافحة الآفات لديك — التوثيق ومراقبة الأجهزة والشهادات التي تحوّل مكافحة الآفات إلى دليل امتثال.", read: "قراءة ٩ دقائق" },
  },
];
