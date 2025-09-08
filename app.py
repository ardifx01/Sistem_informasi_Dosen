from flask import Flask, render_template, request, redirect, url_for, session, flash, send_from_directory, send_file
from flask_session import Session
from werkzeug.security import generate_password_hash, check_password_hash
from redis import Redis
import sqlite3
from datetime import datetime, timedelta
import os
from werkzeug.utils import secure_filename
import calendar
import pandas as pd
import io
import logging   # <--- ini baris import logging

# setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

app = Flask(__name__)   # inisialisasi app Flask
app.logger.info("Aplikasi Flask sudah start 🚀")   # logging pertama kali

# --- Upload folder (buat default & set ke config) ---
UPLOAD_FOLDER = os.path.join(os.getcwd(), "uploads")
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
# Pastikan ukuran maksimal upload (opsional): misal 16 MB
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024


# --- SECRET KEY ---
SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    # Kalau di production tapi SECRET_KEY kosong → langsung error
    if os.getenv("RAILWAY_STATIC_URL") or os.getenv("FLASK_ENV") == "production":
        raise RuntimeError("SECRET_KEY tidak ditemukan di environment. Set di Railway > Variables.")
    # Fallback hanya untuk development lokal
    SECRET_KEY = "dev-secret-key"

app.secret_key = SECRET_KEY

# --- Konfigurasi Session ---
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_TYPE"] = os.getenv("SESSION_TYPE", "filesystem")

# Cookie Hardening
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = bool(
    os.getenv("RAILWAY_STATIC_URL") or os.getenv("FLASK_ENV") == "production"
)

# Redis (jika SESSION_TYPE=redis)
if app.config["SESSION_TYPE"] == "redis":
    app.config["SESSION_REDIS"] = Redis.from_url(os.getenv("SESSION_REDIS"))

Session(app)

# --- Konfigurasi Path Database ---
DATABASE = os.getenv("DATABASE", "database.db")

# --- Fungsi Bantuan ---
def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

# --- Rute Utama dan Login ---
@app.route('/')
def index():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    
    if 'user_id' in session:
        role = session.get('user_role')
        if role == 'Dosen':
            return redirect(url_for('dashboard_dosen'))
        elif role == 'Kajur':
            return redirect(url_for('dashboard_kajur'))
        elif role == 'Admin':
            return redirect(url_for('dashboard_admin'))
        else:
            # role aneh -> bersihkan session supaya tidak stuck
            session.clear()
            
    if request.method == 'POST':
        nip = request.form['nip']
        password = request.form['password']
        conn = get_db_connection()
        user = conn.execute('SELECT * FROM users WHERE nip = ? AND password = ?', (nip, password)).fetchone()
        conn.close()
        # if user and check_password_hash(user['password'], password):
        #     session['user_id'] = user['nip']
        #     session['user_name'] = user['nama_lengkap']
        #     session['user_role'] = user['role']
        #     session['user_jurusan'] = user['jurusan']

        #     if user['role'] == 'Dosen':
        #         return redirect(url_for('dashboard_dosen'))
        #     elif user['role'] == 'Kajur':
        #         return redirect(url_for('dashboard_kajur'))
        #     elif user['role'] == 'Admin': 
        #         return redirect(url_for('dashboard_admin'))
        # else:
        #     error = 'NIP atau Password salah.'

   
        if user:
            session['user_id'] = user['nip']
            session['user_name'] = user['nama_lengkap']
            session['user_role'] = user['role']
            session['user_jurusan'] = user['jurusan']
            
            if user['role'] == 'Dosen':
                return redirect(url_for('dashboard_dosen'))
            elif user['role'] == 'Kajur':
                return redirect(url_for('dashboard_kajur'))
            elif user['role'] == 'Admin': 
                return redirect(url_for('dashboard_admin'))
        else:
            error = 'NIP atau Password salah.'
    return render_template('login.html', error=error)

# Handle Back Session 
@app.after_request
def add_no_cache_headers(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

# --- Rute Dosen ---

@app.route('/dashboard_dosen')
def dashboard_dosen():
    if 'user_role' not in session or session['user_role'] != 'Dosen':
        return redirect(url_for('login'))

    conn = get_db_connection()
    user_nip = session['user_id']

    # --- SEMUA OPERASI DATABASE DILAKUKAN DI SINI ---
    
    # 1. Mengambil data riwayat absensi
    records_raw = conn.execute('SELECT *, rowid as id FROM attendance WHERE nip = ? ORDER BY tanggal DESC', (user_nip,)).fetchall()
    
    # 2. Mengambil jatah cuti tahunan dosen
    user_data = conn.execute("SELECT jatah_cuti_tahunan FROM users WHERE nip = ?", (user_nip,)).fetchone()
    
    # 3. Menghitung total cuti yang sudah terpakai tahun ini
    current_year = str(datetime.now().year)
    total_cuti_terpakai_data = conn.execute(
        """
        SELECT COUNT(*) as total FROM attendance 
        WHERE nip = ? 
        AND status = 'Disetujui Kajur' 
        AND keterangan LIKE '%Cuti%'
        AND strftime('%Y', tanggal) = ?
        """,
        (user_nip, current_year)
    ).fetchone()

    # --- BLOK BARU: HITUNG KUOTA LUPA ABSEN BULAN INI ---
    current_month = datetime.now().strftime('%Y-%m')
    lupa_masuk_data = conn.execute("SELECT COUNT(*) as total FROM clarifications WHERE nip = ? AND jenis_surat = 'Lupa Absen Masuk' AND strftime('%Y-%m', tanggal_pengajuan) = ?", (user_nip, current_month)).fetchone()
    lupa_pulang_data = conn.execute("SELECT COUNT(*) as total FROM clarifications WHERE nip = ? AND jenis_surat = 'Lupa Absen Pulang' AND strftime('%Y-%m', tanggal_pengajuan) = ?", (user_nip, current_month)).fetchone()
    
    lupa_masuk_count = lupa_masuk_data['total'] if lupa_masuk_data else 0
    lupa_pulang_count = lupa_pulang_data['total'] if lupa_pulang_data else 0
    # --- AKHIR BLOK BARU ---

    conn.close() 

    # --- KALKULASI DAN PEMROSESAN DATA (TANPA AKSES DB) ---

    jatah_cuti_tahunan = user_data['jatah_cuti_tahunan'] if user_data and user_data['jatah_cuti_tahunan'] is not None else 0
    total_cuti_terpakai = total_cuti_terpakai_data['total'] if total_cuti_terpakai_data else 0
    sisa_cuti_tahunan = jatah_cuti_tahunan - total_cuti_terpakai
    
    processed_records = []
    # ... (Sisa dari blok pemrosesan Anda tidak diubah sama sekali)
    for record in records_raw:
        rec = dict(record)
        rec['tanggal_formatted'] = datetime.strptime(rec['tanggal'], '%Y-%m-%d %H:%M:%S').strftime('%d/%m/%Y')
        jam_masuk, jam_pulang = rec.get('jam masuk'), rec.get('jam pulang')
        time_fmt = '%H:%M:%S.%f'
        rec['jam_masuk_formatted'] = datetime.strptime(jam_masuk, time_fmt).strftime('%H:%M') if jam_masuk else " - "
        rec['jam_pulang_formatted'] = datetime.strptime(jam_pulang, time_fmt).strftime('%H:%M') if jam_pulang else " - "
        status, keterangan = rec['status'], rec['keterangan']
        
        if status and status.strip() == "Menunggu Persetujuan Kajur":
            rec.update({'status_text': 'Menunggu Persetujuan Kajur', 'status_color': 'yellow', 'checkbox_enabled': False})
        elif status and status.strip() == "Disetujui Kajur":
            if keterangan:
                rec.update({'status_text': keterangan, 'status_color': 'green', 'checkbox_enabled': False})
            else:
                rec.update({'status_text': 'Disetujui Kajur', 'status_color': 'green', 'checkbox_enabled': False})
        elif status and status.strip() == "Ditolak Kajur":
            rec.update({'status_text': f"Ditolak: {keterangan}", 'status_color': 'red', 'checkbox_enabled': True})
        elif jam_masuk and jam_pulang:
            dur = datetime.strptime(jam_pulang, time_fmt) - datetime.strptime(jam_masuk, time_fmt)
            if dur.total_seconds() >= 4 * 3600:
                rec.update({'status_text': 'Kehadiran Terpenuhi', 'status_color': 'green', 'checkbox_enabled': False})
            else:
                rec.update({'status_text': 'Kehadiran Kurang Dari 4 Jam', 'status_color': 'red', 'checkbox_enabled': False})
        else:
            rec.update({'status_text': 'Perlu Klarifikasi', 'status_color': 'red', 'checkbox_enabled': True})
        processed_records.append(rec)
        
    # Mengirim semua data (termasuk data kuota) ke template
    return render_template(
        'dashboard_dosen.html',
        records=processed_records,
        records_json=processed_records,
        jatah_cuti=jatah_cuti_tahunan,
        cuti_terpakai=total_cuti_terpakai,
        sisa_cuti=sisa_cuti_tahunan,
        lupa_masuk_count=lupa_masuk_count,
        lupa_pulang_count=lupa_pulang_count
    )


@app.route('/submit_klarifikasi', methods=['POST'])
def submit_klarifikasi():
    if 'user_role' not in session or session['user_role'] != 'Dosen':
        return redirect(url_for('login'))
        
    record_ids = request.form.getlist('record_ids')
    # Ambil jenis surat dari form untuk divalidasi
    jenis_surat = request.form.get('jenis_surat') 
    
    if not record_ids:
        flash("Anda harus memilih setidaknya satu tanggal untuk diklarifikasi.", "error")
        return redirect(url_for('dashboard_dosen'))
    
    conn = None 
    try:
        conn = get_db_connection()

        # --- BLOK BARU: VALIDASI BATAS MAKSIMAL PENGAJUAN ---
        if jenis_surat in ["Lupa Absen Masuk", "Lupa Absen Pulang"]:
            current_month = datetime.now().strftime('%Y-%m')
            nip = session['user_id']
            
            # Hitung berapa kali jenis surat ini sudah diajukan di bulan ini
            count_data = conn.execute("""
                SELECT COUNT(*) as total FROM clarifications 
                WHERE nip = ? AND jenis_surat = ? AND strftime('%Y-%m', tanggal_pengajuan) = ?
            """, (nip, jenis_surat, current_month)).fetchone()
            
            existing_count = count_data['total'] if count_data else 0

            # Jika sudah 2x atau lebih, gagalkan proses
            if existing_count >= 2:
                # Koneksi ditutup di dalam blok finally, jadi kita tidak perlu menutupnya di sini
                flash(f"GAGAL: Anda sudah mencapai batas maksimal (2x) untuk pengajuan '{jenis_surat}' di bulan ini.", "error")
                return redirect(url_for('dashboard_dosen'))
        # --- AKHIR BLOK BARU ---

        # Jika validasi lolos, lanjutkan proses seperti biasa (tidak ada perubahan di bawah ini)
        file_path = None
        if 'file_bukti' in request.files:
            file = request.files['file_bukti']
            if file.filename != '':
                timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
                filename = secure_filename(f"{session['user_id']}-{timestamp}-{file.filename}")
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(file_path)

        for record_id in record_ids:
            att_rec = conn.execute("SELECT tanggal FROM attendance WHERE rowid = ?", (record_id,)).fetchone()
            if not att_rec:
                continue

            conn.execute("""
                INSERT INTO clarifications (nip, nama_lengkap, jurusan, tanggal_klarifikasi, kategori_surat, jenis_surat, file_bukti)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                session.get('user_id'), 
                session.get('user_name'),
                session.get('user_jurusan'),
                att_rec['tanggal'],
                request.form['kategori_surat'], 
                request.form['jenis_surat'], 
                file_path
            ))
            
            conn.execute("UPDATE attendance SET status = 'Menunggu Persetujuan Kajur', keterangan = '' WHERE rowid = ?", (record_id,))

        conn.commit()
        flash("Klarifikasi berhasil diajukan dan sedang menunggu persetujuan.", "success")

    except Exception as e:
        if conn:
            conn.rollback()
        flash(f"Terjadi kesalahan saat mengajukan klarifikasi: {e}", "error")
        print(f"ERROR submit_klarifikasi: {e}")

    finally:
        if conn:
            conn.close()
            
    return redirect(url_for('dashboard_dosen'))

# --- Rute Kajur ---
@app.route('/dashboard_kajur')
def dashboard_kajur():
    if 'user_role' not in session or session['user_role'] != 'Kajur':
        return redirect(url_for('login'))

    kajur_jurusan = session['user_jurusan']
    conn = get_db_connection()

    # 1. Ambil data klarifikasi yang menunggu (seperti sebelumnya)
    pending_clarifications = conn.execute(
        "SELECT * FROM clarifications WHERE status = 'Menunggu Kajur' AND jurusan = ?",
        (kajur_jurusan,)
    ).fetchall()

    # 2. TAMBAHAN: Ambil daftar dosen di jurusan yang sama
    dosen_list = conn.execute(
        "SELECT * FROM users WHERE jurusan = ? AND role = 'Dosen' ORDER BY nama_lengkap",
        (kajur_jurusan,)
    ).fetchall()

    conn.close()

    # Kirim kedua data ke halaman HTML
    return render_template('dashboard_kajur.html', records=pending_clarifications, dosen_list=dosen_list)

@app.route('/proses_klarifikasi', methods=['POST'])
def proses_klarifikasi():
    if 'user_role' not in session or session['user_role'] != 'Kajur':
        return redirect(url_for('login'))
    
    clarification_id = request.form['clarification_id']
    action = request.form.get('action')
    conn = get_db_connection()
    clarif_rec = conn.execute("SELECT * FROM clarifications WHERE id = ?", (clarification_id,)).fetchone()
    dosen_nip, att_date = clarif_rec['nip'], clarif_rec['tanggal_klarifikasi'].split(' ')[0]
    
    if action == 'setuju':
        conn.execute("UPDATE clarifications SET status = 'Disetujui', tanggal_proses = CURRENT_TIMESTAMP WHERE id = ?", (clarification_id,))
        conn.execute("UPDATE attendance SET status = 'Disetujui Kajur' WHERE nip = ? AND date(tanggal) = ?", (dosen_nip, att_date))
    elif action == 'tolak':
        alasan = request.form.get('alasan_penolakan', '')
        conn.execute("UPDATE clarifications SET status = 'Ditolak', alasan_penolakan = ?, tanggal_proses = CURRENT_TIMESTAMP WHERE id = ?", (alasan, clarification_id))
        conn.execute("UPDATE attendance SET status = 'Ditolak Kajur', keterangan = ? WHERE nip = ? AND date(tanggal) = ?", (alasan, dosen_nip, att_date))
    
    conn.commit()
    conn.close()
    return redirect(url_for('dashboard_kajur'))

# --- Rute Admin ---
@app.route('/dashboard_admin')
def dashboard_admin():
    if 'user_role' not in session or session['user_role'] != 'Admin':
        return redirect(url_for('login'))
    conn = get_db_connection()
    all_users = conn.execute("SELECT * FROM users ORDER BY role, nama_lengkap").fetchall()
    all_history = conn.execute("SELECT * FROM clarifications ORDER BY tanggal_pengajuan DESC").fetchall()
    conn.close()
    return render_template('dashboard_admin.html', users=all_users, histories=all_history)

@app.route('/tambah_pengguna', methods=['GET', 'POST'])
def tambah_pengguna():
    if 'user_role' not in session or session['user_role'] != 'Admin':
        return redirect(url_for('login'))
    if request.method == 'POST':
        nip = request.form['nip']
        password = request.form['password']
        nama_lengkap = request.form['nama_lengkap']
        jurusan = request.form['jurusan']
        detail_jurusan = request.form['detail_jurusan']
        role = request.form['role']
        conn = get_db_connection()
        hashed_password = generate_password_hash(password)
        conn.execute("""
            INSERT INTO users (nip, password, nama_lengkap, jurusan, "detail jurusan", role)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (nip, hashed_password, nama_lengkap, jurusan, detail_jurusan, role))
        # conn.execute("""
        #     INSERT INTO users (nip, password, nama_lengkap, jurusan, "detail jurusan", role)
        #     VALUES (?, ?, ?, ?, ?, ?)
        # """, (nip, password, nama_lengkap, jurusan, detail_jurusan, role))
        conn.commit()
        conn.close()
        return redirect(url_for('dashboard_admin'))
    return render_template('tambah_pengguna.html')


@app.route('/input_cuti', methods=['GET', 'POST'])
def input_cuti():
    if 'user_role' not in session or session['user_role'] != 'Admin':
        return redirect(url_for('login'))

    if request.method == 'POST':
        nip = request.form['nip']
        nama_lengkap = request.form['nama_lengkap']
        tanggal_surat = request.form['tanggal_surat']
        start_date_str = request.form['start_date']
        end_date_str = request.form['end_date']
        jenis_cuti = request.form['jenis_cuti']
        alasan_cuti = request.form.get('alasan_cuti', '')

        conn = get_db_connection()
        
        # 1. Validasi NIP
        dosen_info = conn.execute("SELECT * FROM users WHERE nip = ?", (nip,)).fetchone()
        if not dosen_info:
            conn.close()
            flash(f"GAGAL: NIP '{nip}' tidak ditemukan di database.", "error")
            return redirect(url_for('input_cuti'))

        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        
        # 2. Validasi setiap tanggal absensi sebelum diproses
        dates_to_update = []
        current_date_check = start_date
        while current_date_check <= end_date:
            if current_date_check.weekday() < 5: # 0=Senin, 4=Jumat
                date_str_check = current_date_check.strftime('%Y-%m-%d')
                
                record = conn.execute("SELECT rowid, \"jam masuk\", \"jam pulang\" FROM attendance WHERE nip = ? AND date(tanggal) = ?", (nip, date_str_check)).fetchone()
                
                if not record or (record['jam masuk'] is not None or record['jam pulang'] is not None):
                    conn.close()
                    error_reason = "sudah terisi (Kehadiran Terpenuhi atau Perlu Klarifikasi)"
                    if not record:
                        error_reason = "tidak ditemukan"
                    
                    flash(f"GAGAL: Input cuti untuk tanggal {date_str_check} tidak diizinkan karena data absensi {error_reason}.", "error")
                    return redirect(url_for('input_cuti'))
                
                dates_to_update.append(record['rowid'])
            current_date_check += timedelta(days=1)
        
        # 3. Validasi sisa cuti jika jenisnya 'Cuti Tahunan'
        requested_workdays = len(dates_to_update)
        if jenis_cuti == 'Cuti Tahunan':
            user_data = conn.execute("SELECT jatah_cuti_tahunan FROM users WHERE nip = ?", (nip,)).fetchone()
            jatah_cuti_tahunan = user_data['jatah_cuti_tahunan']
            current_year = str(datetime.now().year)
            total_cuti_terpakai_data = conn.execute(
                "SELECT COUNT(*) as total FROM attendance WHERE nip = ? AND status = 'Disetujui Kajur' AND keterangan LIKE '%Cuti Tahunan%' AND strftime('%Y', tanggal) = ?",
                (nip, current_year)
            ).fetchone()
            total_cuti_terpakai = total_cuti_terpakai_data['total']
            sisa_cuti = jatah_cuti_tahunan - total_cuti_terpakai
            if requested_workdays > sisa_cuti:
                conn.close()
                flash(f"GAGAL: Jatah cuti tidak cukup. Sisa {sisa_cuti}, diminta {requested_workdays}.", "error")
                return redirect(url_for('input_cuti'))

        # 4. Jika semua validasi lolos, proses data
        file_path = None
        if 'file_surat_cuti' in request.files:
            file = request.files['file_surat_cuti']
            if file.filename != '':
                filename = secure_filename(f"cuti-{nip}-{file.filename}")
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(file_path)
        
        conn.execute("""
            INSERT INTO cuti_dosen (nip, nama_lengkap, tanggal_surat, tanggal_mulai, tanggal_selesai, 
                                    jenis_cuti, alasan_cuti, file_surat_cuti, diinput_oleh)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (nip, nama_lengkap, tanggal_surat, start_date_str, end_date_str, jenis_cuti, 
              alasan_cuti, file_path, session.get('user_name')))
        
        keterangan_lengkap = f"{jenis_cuti} - {alasan_cuti}" if alasan_cuti else jenis_cuti
        
        for row_id in dates_to_update:
            conn.execute("UPDATE attendance SET status = 'Disetujui Kajur', keterangan = ? WHERE rowid = ?", (keterangan_lengkap, row_id))
        
        conn.commit()
        conn.close()
        flash(f"Cuti berhasil diperbarui untuk {requested_workdays} hari kerja.", "success")
        return redirect(url_for('input_cuti'))

    # Bagian GET (menampilkan halaman)
    conn = get_db_connection()
    dosen_rows = conn.execute("SELECT nip, nama_lengkap FROM users WHERE role = 'Dosen' ORDER BY nama_lengkap").fetchall()
    list_dosen = [dict(row) for row in dosen_rows]
    history_cuti = conn.execute("SELECT * FROM cuti_dosen ORDER BY tanggal_input DESC").fetchall()
    conn.close()
    return render_template('input_cuti.html', list_dosen=list_dosen, histories=history_cuti)

# TAMBAHKAN FUNGSI BARU INI UNTUK FITUR POP-UP
@app.route('/get_absensi_summary/<nip>')
def get_absensi_summary(nip):
    if 'user_role' not in session or (session['user_role'] != 'Admin' and session['user_role'] != 'Kajur'):
        return {"error": "Unauthorized"}, 403

    conn = get_db_connection()
    dosen = conn.execute("SELECT nama_lengkap FROM users WHERE nip = ?", (nip,)).fetchone()
    nama_lengkap = dosen['nama_lengkap'] if dosen else 'Tidak Ditemukan'

    # --- BLOK BARU: KALKULASI SISA CUTI ---
    # Logika ini kita ambil dari dashboard_dosen dan sesuaikan
    user_data = conn.execute("SELECT jatah_cuti_tahunan FROM users WHERE nip = ?", (nip,)).fetchone()
    jatah_cuti_tahunan = user_data['jatah_cuti_tahunan'] if user_data and user_data['jatah_cuti_tahunan'] is not None else 0
    
    current_year = str(datetime.now().year)
    total_cuti_terpakai_data = conn.execute(
        """
        SELECT COUNT(*) as total FROM attendance 
        WHERE nip = ? 
        AND status = 'Disetujui Kajur' 
        AND keterangan LIKE '%Cuti%'
        AND strftime('%Y', tanggal) = ?
        """,
        (nip, current_year)
    ).fetchone()
    total_cuti_terpakai = total_cuti_terpakai_data['total'] if total_cuti_terpakai_data else 0
    sisa_cuti_tahunan = jatah_cuti_tahunan - total_cuti_terpakai
    # --- AKHIR BLOK BARU ---

    last_month_record = conn.execute(
        "SELECT strftime('%Y-%m', tanggal) as last_month FROM attendance WHERE nip = ? ORDER BY tanggal DESC LIMIT 1",
        (nip,)
    ).fetchone()

    processed_records = []
    if last_month_record:
        target_month = last_month_record['last_month']
        records_raw = conn.execute(
            "SELECT * FROM attendance WHERE nip = ? AND strftime('%Y-%m', tanggal) = ? ORDER BY tanggal DESC", 
            (nip, target_month)
        ).fetchall()

        # ... (Sisa dari blok pemrosesan absensi Anda tidak berubah sama sekali) ...
        time_fmt = '%H:%M:%S.%f'
        for record in records_raw:
            rec = dict(record)
            rec['tanggal_formatted'] = datetime.strptime(rec['tanggal'], '%Y-%m-%d %H:%M:%S').strftime('%d/%m/%Y')
            jam_masuk, jam_pulang = rec.get('jam masuk'), rec.get('jam pulang')
            status, keterangan = rec['status'], rec['keterangan']
            rec['jam_masuk_formatted'] = datetime.strptime(jam_masuk, time_fmt).strftime('%H:%M') if jam_masuk else "-"
            rec['jam_pulang_formatted'] = datetime.strptime(jam_pulang, time_fmt).strftime('%H:%M') if jam_pulang else "-"
            if status == "Disetujui Kajur":
                rec['status_text'] = keterangan if keterangan else "Disetujui Kajur"
                rec['status_color'] = "status-green"
            elif status == "Ditolak Kajur":
                rec['status_text'] = f"Ditolak: {keterangan}"
                rec['status_color'] = "status-red"
            elif status == "Menunggu Persetujuan Kajur":
                rec['status_text'] = "Menunggu Persetujuan"
                rec['status_color'] = "status-yellow"
            elif jam_masuk and jam_pulang:
                dur = datetime.strptime(jam_pulang, time_fmt) - datetime.strptime(jam_masuk, time_fmt)
                if dur.total_seconds() >= 4 * 3600:
                    rec['status_text'] = "Kehadiran Terpenuhi"
                    rec['status_color'] = "status-green"
                else:
                    rec['status_text'] = "Kurang Dari 4 Jam"
                    rec['status_color'] = "status-red"
            else:
                rec['status_text'] = "Perlu Klarifikasi"
                rec['status_color'] = "status-red"
            processed_records.append(rec)

    conn.close()

    # --- PERBARUI DATA YANG DIKEMBALIKAN (RETURN) ---
    return {
        "nama_lengkap": nama_lengkap,
        "records": processed_records,
        "jatah_cuti": jatah_cuti_tahunan,
        "cuti_terpakai": total_cuti_terpakai,
        "sisa_cuti": sisa_cuti_tahunan
    }

@app.route('/rekap_laporan_view')
def rekap_laporan_view():
    if 'user_role' not in session or session['user_role'] != 'Admin':
        return redirect(url_for('login'))

    try:
        conn = get_db_connection()

        # --- Bagian Pengambilan Data (Lebih Efisien) ---
        target_month = '2025-07' # Fokus ke bulan Juli
        year, month = map(int, target_month.split('-'))

        # Ambil semua dosen terlebih dahulu
        all_dosen = conn.execute("SELECT nip, nama_lengkap, jurusan, \"detail jurusan\" FROM users WHERE role = 'Dosen' ORDER BY jurusan, nama_lengkap").fetchall()

        # Ambil semua data absensi & klarifikasi untuk bulan target
        attendance_data = conn.execute("SELECT * FROM attendance WHERE strftime('%Y-%m', tanggal) = ?", (target_month,)).fetchall()
        approved_clarifications = conn.execute("SELECT nip, tanggal_klarifikasi, kategori_surat FROM clarifications WHERE strftime('%Y-%m', tanggal_klarifikasi) = ? AND status = 'Disetujui'", (target_month,)).fetchall()
        
        conn.close()

        # --- Bagian Pemrosesan Data ---
        num_days = calendar.monthrange(year, month)[1]
        days_in_month = list(range(1, num_days + 1))
        selected_bulan_formatted = datetime(year, month, 1).strftime("%B %Y")

        # Siapkan struktur data untuk laporan
        report_data = {} # Menggunakan dictionary agar lebih mudah diakses
        for dosen in all_dosen:
            jurusan = dosen['jurusan']
            detail_jurusan = dosen['detail jurusan']
            if jurusan not in report_data:
                report_data[jurusan] = {
                    'nama_jurusan': detail_jurusan,
                    'dosen_data': {}
                }
            report_data[jurusan]['dosen_data'][dosen['nip']] = {
                'nama': dosen['nama_lengkap'],
                'absensi': {},
                'summary_counts': {'KT': 0, 'PK': 0, 'NF': 0, 'FL': 0, 'CT': 0, 'IZ': 0}
            }

        # Buat dictionary untuk klarifikasi agar pencarian cepat
        clarif_dict = {(item['nip'], item['tanggal_klarifikasi'].split(' ')[0]): item['kategori_surat'] for item in approved_clarifications}

        # Proses data absensi (hanya satu kali perulangan)
        # Proses data absensi (hanya satu kali perulangan)
        for record in attendance_data:
            # PERBAIKAN: Gunakan ['...'] bukan .get('...')
            nip = record['nip']
            
            # Pengecekan NIP tetap sama
            if not nip or not any(nip in data['dosen_data'] for data in report_data.values()):
                continue # Lewati jika NIP tidak ada di daftar dosen

            try:
                tanggal_obj = datetime.strptime(record['tanggal'], '%Y-%m-%d %H:%M:%S')
                day = tanggal_obj.day
                tanggal_str = tanggal_obj.strftime('%Y-%m-%d')
                
                status = record['status']
                jam_masuk = record['jam masuk']
                jam_pulang = record['jam pulang']
                keterangan = record['keterangan'] if 'keterangan' in record.keys() else ''

                kode = 'PK' # Tetap sebagai default

                # --- PERBAIKAN LOGIKA STATUS ---
                if status == 'Disetujui Kajur':
                    if 'Cuti' in keterangan:
                        kode = 'CT'
                    elif (nip, tanggal_str) in clarif_dict:
                        kategori = clarif_dict.get((nip, tanggal_str))
                        if kategori and 'Non Fleksibel' in kategori: kode = 'NF'
                        elif kategori and 'Fleksibel' in kategori: kode = 'FL'
                        else: kode = 'IZ'
                    else:
                        kode = 'IZ'
                # Tambahkan kondisi untuk status 'Hadir'
                elif status == 'Hadir' and jam_masuk and jam_pulang:
                    # --- PERBAIKAN FORMAT WAKTU ---
                    # Kembalikan format ke %H:%M:%S.%f sesuai data di database
                    dur = datetime.strptime(jam_pulang, '%H:%M:%S.%f') - datetime.strptime(jam_masuk, '%H:%M:%S.%f')
                    if dur.total_seconds() >= 4 * 3600: # 4 jam
                        kode = 'KT'
                
                # Masukkan data ke struktur laporan
                for jurusan_data in report_data.values():
                    if nip in jurusan_data['dosen_data']:
                        jurusan_data['dosen_data'][nip]['absensi'][day] = kode
                        jurusan_data['dosen_data'][nip]['summary_counts'][kode] += 1
                        break

            except Exception as e:
                # Blok ini akan menangkap jika ada error lain yang tak terduga
                print(f"Melewatkan data error untuk NIP {nip} pada tanggal {record.get('tanggal')}: {e}")
                continue

        # Finalisasi data untuk dikirim ke template
        report_data_per_jurusan = []
        for jurusan_key, data in report_data.items():
            dosen_list = []
            for nip, dosen_data in data['dosen_data'].items():
                summary_counts = dosen_data['summary_counts']
                summary_str = ", ".join([f"{k}:{v}" for k, v in summary_counts.items() if v > 0])
                dosen_data['summary'] = summary_str
                dosen_list.append(dosen_data)
            
            report_data_per_jurusan.append({
                'nama_jurusan': data['nama_jurusan'],
                'dosen_data': dosen_list
            })

        session['report_for_download'] = report_data_per_jurusan
        return render_template('rekap_laporan.html', report_data=report_data_per_jurusan, days_in_month=days_in_month, selected_bulan_formatted=selected_bulan_formatted)

    except Exception as e:
        # Tambahkan traceback untuk debugging yang lebih mudah di log server
        import traceback
        print(f"Terjadi Internal Server Error: {e}")
        traceback.print_exc()
        return "Terjadi kesalahan saat memproses laporan. Silakan periksa format data Anda atau hubungi administrator.", 500

# FUNGSI BARU UNTUK DOWNLOAD
@app.route('/download_laporan')
def download_laporan():
    if 'user_role' not in session or session['user_role'] != 'Admin':
        return redirect(url_for('login'))

    report_data_per_jurusan = session.get('report_for_download')
    if not report_data_per_jurusan:
        return "Tidak ada data untuk di-download.", 404

    # Gabungkan semua data menjadi satu list untuk Excel
    final_data_for_excel = []
    for jurusan_data in report_data_per_jurusan:
        for dosen in jurusan_data['dosen_data']:
            row_data = {
                'Jurusan': jurusan_data['nama_jurusan'],
                'Nama': dosen['nama'],
            }
            # Mengisi tanggal
            for day in range(1, 32): # Asumsi maksimal 31 hari
                row_data[day] = dosen['absensi'].get(day, '')
            row_data['Jumlah'] = dosen['summary']
            final_data_for_excel.append(row_data)

    df = pd.DataFrame(final_data_for_excel)
    output = io.BytesIO()
    writer = pd.ExcelWriter(output, engine='xlsxwriter')
    df.to_excel(writer, index=False, sheet_name='Rekap Absensi Juli')
    writer.close()
    output.seek(0)

    return send_file(output, as_attachment=True, download_name='Rekap_Absensi_Juli.xlsx', 
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.route('/riwayat_cuti')
def riwayat_cuti():
    # Pastikan hanya dosen yang bisa mengakses halaman riwayatnya sendiri
    if 'user_role' not in session or session['user_role'] != 'Dosen':
        return redirect(url_for('login'))

    nip = session['user_id']
    nama_dosen = session['user_name']
    
    conn = get_db_connection()
    # Ambil semua data dari tabel cuti_dosen untuk NIP yang sedang login
    cuti_records = conn.execute(
        "SELECT * FROM cuti_dosen WHERE nip = ? ORDER BY tanggal_mulai DESC", 
        (nip,)
    ).fetchall()
    conn.close()

    return render_template('riwayat_cuti.html', riwayat=cuti_records, nama_dosen=nama_dosen)


                            
# --- Rute Riwayat dan Lain-lain ---
@app.route('/history')
def history():
    if 'user_role' not in session:
        return redirect(url_for('login'))
    conn = get_db_connection()
    if session['user_role'] == 'Dosen':
        history_records = conn.execute("SELECT * FROM clarifications WHERE nip = ? ORDER BY tanggal_pengajuan DESC", (session['user_id'],)).fetchall()
    elif session['user_role'] == 'Kajur':
        history_records = conn.execute("SELECT * FROM clarifications WHERE jurusan = ? ORDER BY tanggal_pengajuan DESC", (session['user_jurusan'],)).fetchall()
    else:
        history_records = []
    conn.close()
    return render_template('history.html', records=history_records)

@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# if __name__ == '__main__':
#     if not os.path.exists(UPLOAD_FOLDER):
#         os.makedirs(UPLOAD_FOLDER)
#     app.run(debug=True)

# if __name__ == "__main__":
#     # Pastikan folder upload ada
#     if not os.path.exists(app.config["UPLOAD_FOLDER"]):
#         os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

# if __name__ == "__main__":
#     port = int(os.environ.get("PORT", 5000))
#     app.run(host="0.0.0.0", port=port)

    # Baca debug dari ENV
# debug_mode = str(os.environ.get("FLASK_DEBUG", "0")).lower() in ("1", "true", "yes")

    # Port default 5000 (lokal), Railway inject $PORT
# port = int(os.environ.get("PORT", 5000))

if __name__ == "__main__":
    # Hanya untuk lokal development
    if not os.path.exists(app.config["UPLOAD_FOLDER"]):
        os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    debug_mode = str(os.environ.get("FLASK_DEBUG", "0")).lower() in ("1", "true", "yes")
    port = int(os.environ.get("PORT", 5000))

    app.run(host="0.0.0.0", port=port, debug=debug_mode)
