"""Seed the CRM with demo data (bilingual). Run automatically on first launch,
or manually:  python3 seed.py
"""
import database as db
import auth


def run():
    db.init_db()
    if db.query("SELECT COUNT(*) c FROM users", one=True)["c"] > 0:
        print("Data already present; skipping seed.")
        return

    # --- service types ---
    services = [
        ("General Pest Control", "مكافحة الآفات العامة"),
        ("Rodent Control", "مكافحة القوارض"),
        ("Termite Treatment", "مكافحة النمل الأبيض"),
        ("Cockroach Treatment", "مكافحة الصراصير"),
        ("Bed Bugs Treatment", "مكافحة بق الفراش"),
        ("Fumigation", "التبخير"),
        ("Mosquito Control", "مكافحة البعوض"),
    ]
    svc_ids = [db.execute("INSERT INTO service_types(name_en,name_ar) VALUES(?,?)", s) for s in services]

    # --- clients (company folders) ---
    clients = [
        ("Al Noor Restaurant", "مطعم النور", "Khaled Mostafa", "+201000000001",
         "info@alnoor.com", "Tahrir Square, Cairo", "ميدان التحرير، القاهرة", "Cairo"),
        ("Nile View Hotel", "فندق إطلالة النيل", "Sara Mansour", "+201000000002",
         "contact@nileview.com", "Corniche El Nil, Cairo", "كورنيش النيل، القاهرة", "Cairo"),
        ("Green Valley School", "مدرسة الوادي الأخضر", "Ahmed Fares", "+201000000003",
         "admin@greenvalley.edu", "Smouha, Alexandria", "سموحة، الإسكندرية", "Alexandria"),
        ("Fresh Mart Supermarket", "سوبرماركت فريش مارت", "Laila Hassan", "+201000000004",
         "ops@freshmart.com", "6th of October City, Giza", "مدينة السادس من أكتوبر، الجيزة", "Giza"),
    ]
    client_ids = []
    for c in clients:
        cid = db.execute(
            "INSERT INTO clients(name_en,name_ar,contact_person,phone,email,address_en,address_ar,city) "
            "VALUES(?,?,?,?,?,?,?,?)", c)
        client_ids.append(cid)

    # sites
    db.execute("INSERT INTO sites(client_id,name,address,area) VALUES(?,?,?,?)",
               (client_ids[1], "Main Building", "Corniche, Jeddah", "Lobby & Kitchen"))
    db.execute("INSERT INTO sites(client_id,name,address,area) VALUES(?,?,?,?)",
               (client_ids[1], "Annex", "Corniche, Jeddah", "Storage"))

    # --- users ---
    db.execute(
        "INSERT INTO users(full_name,email,password_hash,role,phone,lang) VALUES(?,?,?,?,?,?)",
        ("System Admin", "admin@pestcrm.com", auth.hash_password("admin123"), "admin", "+966555000000", "en"))
    db.execute(
        "INSERT INTO users(full_name,email,password_hash,role,phone,lang) VALUES(?,?,?,?,?,?)",
        ("Manager Mona", "manager@pestcrm.com", auth.hash_password("manager123"), "manager", "+966555000001", "ar"))

    agent1 = db.execute(
        "INSERT INTO users(full_name,email,password_hash,role,phone,specialization,hire_date,lang) "
        "VALUES(?,?,?,?,?,?,?,?)",
        ("Yousef Ali", "agent1@pestcrm.com", auth.hash_password("agent123"), "agent",
         "+966555111111", "Termite & Fumigation", "2023-02-01", "ar"))
    agent2 = db.execute(
        "INSERT INTO users(full_name,email,password_hash,role,phone,specialization,hire_date,lang) "
        "VALUES(?,?,?,?,?,?,?,?)",
        ("Omar Saeed", "agent2@pestcrm.com", auth.hash_password("agent123"), "agent",
         "+966555222222", "General & Rodent", "2022-09-15", "en"))

    db.execute(
        "INSERT INTO users(full_name,email,password_hash,role,phone,client_id,lang) VALUES(?,?,?,?,?,?,?)",
        ("Al Noor Portal", "client@alnoor.com", auth.hash_password("client123"), "client",
         "+966500000001", client_ids[0], "ar"))

    # --- chemicals ---
    chems = [
        ("Cypermethrin 10%", "سايبرمثرين ١٠٪", "Cypermethrin", "L", 50, 10, "Class II", "EPA-1001", 45.0),
        ("Fipronil Gel", "جل الفيبرونيل", "Fipronil", "g", 2000, 500, "Class III", "EPA-2002", 0.12),
        ("Brodifacoum Bait", "طعم البروديفاكوم", "Brodifacoum", "kg", 8, 10, "Class I", "EPA-3003", 30.0),
        ("Imidacloprid 20%", "إيميداكلوبريد ٢٠٪", "Imidacloprid", "L", 25, 5, "Class II", "EPA-4004", 60.0),
        ("Boric Acid Powder", "مسحوق حمض البوريك", "Boric Acid", "kg", 40, 10, "Class IV", "EPA-5005", 8.0),
    ]
    chem_ids = [db.execute(
        "INSERT INTO chemicals(name_en,name_ar,active_ingredient,unit,quantity_in_stock,reorder_level,"
        "hazard_class,reg_no,cost_per_unit) VALUES(?,?,?,?,?,?,?,?,?)", c) for c in chems]

    # --- visits ---
    v1 = db.execute(
        "INSERT INTO visits(client_id,agent_id,service_type_id,scheduled_start,scheduled_end,status,location,notes,completed_at) "
        "VALUES(?,?,?,datetime('now','-7 days'),datetime('now','-7 days','+2 hours'),'completed',?,?,datetime('now','-7 days','+2 hours'))",
        (client_ids[0], agent1, svc_ids[3], "Main kitchen", "Routine cockroach treatment"))
    db.execute("INSERT INTO reports(visit_id,summary,pests_found,findings,recommendations,severity) VALUES(?,?,?,?,?,?)",
               (v1, "Cockroach activity reduced after gel bait application.",
                "German cockroaches", "Heavy activity near sink and behind fridge.",
                "Seal gaps; schedule follow-up in 2 weeks.", "high"))
    db.execute("INSERT INTO chemical_usage(visit_id,chemical_id,quantity,area_treated) VALUES(?,?,?,?)",
               (v1, chem_ids[1], 150, "Kitchen cabinets"))
    db.execute("UPDATE chemicals SET quantity_in_stock = quantity_in_stock - 150 WHERE id=?", (chem_ids[1],))
    db.execute("INSERT INTO inventory_transactions(chemical_id,change,reason,reference) VALUES(?,?,?,?)",
               (chem_ids[1], -150, "usage", f"visit:{v1}"))

    db.execute(
        "INSERT INTO visits(client_id,agent_id,service_type_id,scheduled_start,status,location,notes) "
        "VALUES(?,?,?,datetime('now','+1 days'),'scheduled',?,?)",
        (client_ids[1], agent2, svc_ids[1], "Storage area", "Rodent inspection and baiting"))
    db.execute(
        "INSERT INTO visits(client_id,agent_id,service_type_id,scheduled_start,status,location,notes) "
        "VALUES(?,?,?,datetime('now','+3 days'),'scheduled',?,?)",
        (client_ids[2], agent1, svc_ids[0], "Whole campus", "Quarterly general treatment"))
    db.execute(
        "INSERT INTO visits(client_id,agent_id,service_type_id,scheduled_start,status,location,notes) "
        "VALUES(?,?,?,datetime('now'),'in_progress',?,?)",
        (client_ids[3], agent2, svc_ids[1], "Warehouse", "Monthly rodent control"))

    # --- invoices + payments ---
    inv1 = db.execute(
        "INSERT INTO invoices(client_id,visit_id,number,issue_date,due_date,amount,tax,total,status) "
        "VALUES(?,?,?,date('now','-6 days'),date('now','+9 days'),500,75,575,'sent')",
        (client_ids[0], v1, "INV-00001"))
    db.execute("INSERT INTO payments(invoice_id,amount,method) VALUES(?,?,?)", (inv1, 200, "bank_transfer"))
    inv2 = db.execute(
        "INSERT INTO invoices(client_id,doc_type,number,issue_date,due_date,amount,tax,total,status) "
        "VALUES(?,?,?,date('now','-30 days'),date('now','-15 days'),1200,180,1380,'overdue')",
        (client_ids[1], "invoice", "INV-00002"))

    # --- invoice line items on INV-00001 ---
    db.execute("INSERT INTO invoice_items(invoice_id,description,quantity,unit_price,amount) VALUES(?,?,?,?,?)",
               (inv1, "Cockroach treatment - kitchen", 1, 350, 350))
    db.execute("INSERT INTO invoice_items(invoice_id,description,quantity,unit_price,amount) VALUES(?,?,?,?,?)",
               (inv1, "Monitoring stations (x3)", 3, 50, 150))

    # --- a quote with line items ---
    quo = db.execute(
        "INSERT INTO invoices(client_id,doc_type,number,issue_date,valid_until,amount,tax,total,status) "
        "VALUES(?,?,?,date('now'),date('now','+30 days'),2000,300,2300,'sent')",
        (client_ids[2], "quote", "QUO-00001"))
    db.execute("INSERT INTO invoice_items(invoice_id,description,quantity,unit_price,amount) VALUES(?,?,?,?,?)",
               (quo, "Annual general pest control program", 1, 2000, 2000))

    # --- a recurring contract (quarterly) for Sunrise Hotel ---
    db.execute(
        "INSERT INTO contracts(client_id,service_type_id,agent_id,frequency,start_date,next_run_date,price,status,notes) "
        "VALUES(?,?,?,?,date('now','-100 days'),date('now','-100 days'),800,'active',?)",
        (client_ids[1], svc_ids[0], agent2, "quarterly", "Quarterly general pest control"))

    # --- company settings / branding ---
    settings = {
        "company_name_en": "PestCare Pest Control Co.", "company_name_ar": "شركة بيست كير لمكافحة الآفات",
        "address_en": "Nasr City, Cairo, Egypt", "address_ar": "مدينة نصر، القاهرة، مصر",
        "phone": "+20 2 1234 5678", "email": "billing@pestcare.com",
        "vat_no": "100-200-300", "currency": "EGP", "tax_rate": "14",
    }
    for k, v in settings.items():
        db.execute("INSERT INTO settings(key,value) VALUES(?,?)", (k, v))

    print("Seed complete.")
    print("Logins:")
    print("  admin@pestcrm.com / admin123   (admin)")
    print("  manager@pestcrm.com / manager123 (manager)")
    print("  agent1@pestcrm.com / agent123  (agent)")
    print("  client@alnoor.com / client123  (client portal)")


if __name__ == "__main__":
    run()
