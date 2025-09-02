import pandas as pd
import sqlite3
from werkzeug.security import generate_password_hash

# --- KONFIGURASI ---
# Ubah nama file ini setiap kali Anda ingin migrasi data dari bulan baru
EXCEL_FILE = "db_agustus.xlsx" 
DB_FILE = "database.db"
ATTENDANCE_SHEETS = ['BP', 'BT', 'PKH', 'RPK', 'THP']
USERS_SHEET = 'data_akses'

def run_migration():
    conn = None
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        print(f"Berhasil terhubung ke database {DB_FILE}")

        # --- 1. Migrasi Tabel Users (Mode Cerdas: Tambah/Update) ---
        print("\nMemulai migrasi data user...")
        df_users = pd.read_excel(EXCEL_FILE, sheet_name=USERS_SHEET, dtype={'nip': str})
        
        for index, row in df_users.iterrows():
            nip = row['nip']
            cursor.execute("SELECT nip FROM users WHERE nip = ?", (nip,))
            data_exists = cursor.fetchone()

            if not data_exists:
                # Jika user belum ada, tambahkan user baru
                print(f"  -> Menambahkan user baru: {row['nama_lengkap']}")
                # Password default sama dengan NIP saat user baru dibuat
                hashed_password = generate_password_hash(nip) 
                # Menambahkan 'jatah_cuti_tahunan' dengan nilai default 12
                cursor.execute("""
                    INSERT INTO users (nip, password, nama_lengkap, jurusan, "detail jurusan", role, jatah_cuti_tahunan)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (nip, hashed_password, row['nama_lengkap'], row['jurusan'], row['detail jurusan'], row['role'], 12))
            # Jika user sudah ada, kita tidak melakukan apa-apa untuk menjaga data yang ada (password, jatah_cuti, dll)

        print("-> Migrasi data user selesai.")


        # --- 2. Migrasi Tabel Attendance (Mode Aditif: Hanya Menambah Data Baru) ---
        print("\nMemulai migrasi data absensi...")
        all_dfs = [pd.read_excel(EXCEL_FILE, sheet_name=sheet, dtype={'nip': str}) for sheet in ATTENDANCE_SHEETS]
        df_combined = pd.concat(all_dfs, ignore_index=True)
        
        added_count = 0
        skipped_count = 0
        for index, row in df_combined.iterrows():
            nip_absen = row['nip']
            # Konversi tanggal dari Excel ke format yang konsisten (YYYY-MM-DD)
            tanggal_absen_obj = pd.to_datetime(row['tanggal']).date()
            tanggal_absen_str = tanggal_absen_obj.strftime('%Y-%m-%d')

            # Cek apakah data absensi untuk NIP dan tanggal tersebut sudah ada
            cursor.execute("SELECT rowid FROM attendance WHERE nip = ? AND date(tanggal) = ?", (nip_absen, tanggal_absen_str))
            record_exists = cursor.fetchone()

            if not record_exists:
                # Jika belum ada, masukkan sebagai data baru
                full_tanggal_str = f"{tanggal_absen_str} 00:00:00" # Tambahkan jam default
                cursor.execute("""
                    INSERT INTO attendance (nip, nama_lengkap, jurusan, "detail jurusan", tanggal, "jam masuk", "jam pulang", status, keterangan)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'Hadir', '')
                """, (nip_absen, row['nama_lengkap'], row['jurusan'], row['detail jurusan'], full_tanggal_str, row['jam masuk'], row['jam pulang']))
                added_count += 1
            else:
                skipped_count += 1
        
        print(f"-> Migrasi absensi selesai. Data baru ditambahkan: {added_count}, Data duplikat dilewati: {skipped_count}.")


        # --- 3. Pembuatan Tabel Lainnya (Mode Aman: Hanya Jika Belum Ada) ---
        # Menggunakan "CREATE TABLE IF NOT EXISTS" agar tidak menghapus tabel yang sudah ada
        
        print("\nMemeriksa tabel 'clarifications'...")
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS clarifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT, nip TEXT NOT NULL, nama_lengkap TEXT NOT NULL,
            jurusan TEXT NOT NULL, tanggal_klarifikasi TEXT NOT NULL, kategori_surat TEXT NOT NULL,
            jenis_surat TEXT NOT NULL, file_bukti TEXT, status TEXT DEFAULT 'Menunggu Persetujuan Kajur',
            alasan_penolakan TEXT, tanggal_pengajuan TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            tanggal_proses TIMESTAMP
        );
        """)
        print("-> Tabel 'clarifications' sudah siap.")

        print("\nMemeriksa tabel 'cuti_dosen'...")
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS cuti_dosen (
            id INTEGER PRIMARY KEY AUTOINCREMENT, nip TEXT NOT NULL, nama_lengkap TEXT NOT NULL,
            tanggal_surat TEXT NOT NULL, tanggal_mulai TEXT NOT NULL, tanggal_selesai TEXT NOT NULL,
            jenis_cuti TEXT NOT NULL, alasan_cuti TEXT, file_surat_cuti TEXT, diinput_oleh TEXT,
            tanggal_input TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)
        print("-> Tabel 'cuti_dosen' sudah siap.")
        
        # Simpan semua perubahan ke database
        conn.commit()
        print("\nProses migrasi selesai! Database telah diperbarui dengan aman.")

    except Exception as e:
        print(f"\n[ERROR] Terjadi kesalahan: {e}")
        if conn:
            conn.rollback() # Batalkan semua perubahan jika terjadi error

    finally:
        if conn:
            conn.close() # Pastikan koneksi selalu ditutup

if __name__ == '__main__':
    run_migration()