from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import psycopg2
import psycopg2.extras

import boto3
from datetime import datetime
import os
import logging
from botocore.config import Config



# Enable logging
logging.basicConfig(level=logging.DEBUG)
app = Flask(__name__)
CORS(app, origins=["*"])

# Track initialization
app.initialized = False

# ---------- DATABASE CONNECTION ----------
def get_db_connection():
    """Simple database connection"""
    try:
        conn = psycopg2.connect(
            host="database-1.ch86ai8iox4q.eu-north-1.rds.amazonaws.com",
            database="postgres",
            user="postgres",
            password="postgres",
            connect_timeout=5
        )
        return conn
    except Exception as e:
        print(f"❌ Database error: {e}")
        return None

# ---------- S3 ----------
s3_client = boto3.client('s3', region_name='eu-north-1')
BUCKET = "dental-clinic-reports-1"

# ---------- CREATE TABLES ----------
def create_tables():
    """Create all tables if they don't exist"""
    conn = get_db_connection()
    if not conn:
        return False

    cur = conn.cursor()

    # Patients
    cur.execute("""
        CREATE TABLE IF NOT EXISTS patients (
            id SERIAL PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            email VARCHAR(255) UNIQUE NOT NULL,
            phone VARCHAR(20),
            password VARCHAR(255),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Appointments
    cur.execute("""
        CREATE TABLE IF NOT EXISTS appointments (
            id SERIAL PRIMARY KEY,
            patient_id INTEGER REFERENCES patients(id),
            appointment_date DATE NOT NULL,
            treatment VARCHAR(255),
            status VARCHAR(50) DEFAULT 'Booked',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Reports
    cur.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            id SERIAL PRIMARY KEY,
            patient_id INTEGER REFERENCES patients(id),
            test_name VARCHAR(255),
            status VARCHAR(50) DEFAULT 'Pending',
            s3_key VARCHAR(500),
            upload_date TIMESTAMP,
            requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Admins
    cur.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            id SERIAL PRIMARY KEY,
            username VARCHAR(100) UNIQUE,
            password VARCHAR(100)
        )
    """)

    # Default admin
    cur.execute("SELECT COUNT(*) FROM admins WHERE username='admin'")
    if cur.fetchone()[0] == 0:
        cur.execute("INSERT INTO admins (username, password) VALUES ('admin', 'admin123')")

    conn.commit()
    cur.close()
    conn.close()
    return True

# ---------- INITIALIZE ----------
@app.before_request
def initialize():
    if not app.initialized:
        print("🚀 Initializing database...")
        if create_tables():
            print("✅ Tables ready")
        app.initialized = True

# ---------- ROUTES ----------

@app.route('/')
def home():
    return send_from_directory('patient', 'patient-login.html')

@app.route('/css/<path:filename>')
def serve_css(filename):
    return send_from_directory('css', filename)

@app.route('/patient/<path:filename>')
def serve_patient(filename):
    return send_from_directory('patient', filename)

@app.route('/admin/<path:filename>')
def serve_admin(filename):
    return send_from_directory('admin', filename)

# ---------- PATIENT ----------
@app.route('/patient/register', methods=['POST'])
def patient_register():
    data = request.json
    conn = get_db_connection()
    if not conn:
        return jsonify({"success": False, "error": "Database down"}), 500

    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO patients(name, email, phone, password) VALUES(%s,%s,%s,%s) RETURNING id",
            (data['name'], data['email'], data['phone'], data['password'])
        )
        pid = cur.fetchone()[0]
        conn.commit()
        return jsonify({"success": True, "patient_id": pid, "message": "Registered"})
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "error": str(e)}), 400
    finally:
        cur.close()
        conn.close()

@app.route('/patient/login', methods=['POST'])
def patient_login():
    data = request.json
    conn = get_db_connection()
    if not conn:
        return jsonify({"success": False, "error": "Database down"}), 500

    try:
        cur = conn.cursor()
        login_val = data.get('login', '')
        cur.execute(
            "SELECT id, name FROM patients WHERE (email=%s OR phone=%s) AND password=%s",
            (login_val, login_val, data['password'])
        )
        patient = cur.fetchone()

        if patient:
            return jsonify({
                "success": True,
                "patient_id": patient[0],
                "patient_name": patient[1]
            })
        return jsonify({"success": False, "error": "Wrong credentials"}), 401
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        cur.close()
        conn.close()

@app.route('/book-appointment', methods=['POST'])
def book_appointment():
    data = request.json
    print(f"📅 Booking appointment: {data}")

    conn = get_db_connection()
    if not conn:
        print("❌ Database connection failed")
        return jsonify({"success": False, "error": "Database connection failed"}), 500

    cur = None
    try:
        cur = conn.cursor()

        # Validate data
        if not data.get('patient_id'):
            return jsonify({"success": False, "error": "Patient ID is required"}), 400

        if not data.get('date'):
            return jsonify({"success": False, "error": "Date is required"}), 400

        if not data.get('treatment'):
            return jsonify({"success": False, "error": "Treatment is required"}), 400

        # Insert appointment
        cur.execute(
            "INSERT INTO appointments(patient_id, appointment_date, treatment, status) VALUES(%s, %s, %s, 'Booked') RETURNING id",
            (data['patient_id'], data['date'], data['treatment'])
        )

        appointment_id = cur.fetchone()[0]
        conn.commit()

        print(f"✅ Appointment booked with ID: {appointment_id}")
        return jsonify({
            "success": True,
            "message": "Appointment booked successfully!",
            "appointment_id": appointment_id
        })

    except Exception as e:
        conn.rollback()
        print(f"❌ Database error: {e}")
        return jsonify({"success": False, "error": str(e)}), 400
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

@app.route('/request-report', methods=['POST'])
def submit_report_request():
    """Handle patient report requests"""
    try:
        data = request.get_json()
        print(f"📝 Patient {data.get('patient_id')} requesting report: {data.get('test_name')}")

        conn = get_db_connection()
        if not conn:
            return jsonify({"success": False, "error": "Database down"}), 500

        cur = conn.cursor()

        # Insert report request
        cur.execute(
            "INSERT INTO reports (patient_id, test_name, status) VALUES (%s, %s, 'Pending') RETURNING id",
            (int(data['patient_id']), data['test_name'])
        )

        report_id = cur.fetchone()[0]
        conn.commit()

        print(f"✅ Report saved to database. ID: {report_id}")

        return jsonify({
            "success": True,
            "message": "Report requested successfully",
            "report_id": report_id
        })

    except Exception as e:
        print(f"❌ Error saving report: {e}")
        return jsonify({"success": False, "error": str(e)}), 400
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


@app.route('/admin/report-requests')
def get_report_requests():
    print("📋 Admin: Fetching report requests...")

    conn = get_db_connection()
    if not conn:
        print("❌ No database connection")
        return jsonify([]), 500

    cur = None
    try:
        cur = conn.cursor()

        # First, check if reports table exists
        cur.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_schema = 'public'
                AND table_name = 'reports'
            )
        """)
        table_exists = cur.fetchone()[0]

        if not table_exists:
            print("❌ Reports table doesn't exist")
            return jsonify([])

        # Get all reports with patient names
        cur.execute("""
            SELECT
                r.id,
                r.patient_id,
                COALESCE(p.name, 'Unknown Patient') as patient_name,
                r.test_name,
                COALESCE(r.status, 'Pending') as status,
                r.requested_at,
                r.upload_date,
                r.s3_key
            FROM reports r
            LEFT JOIN patients p ON r.patient_id = p.id
            ORDER BY r.requested_at DESC
        """)

        rows = cur.fetchall()
        print(f"📊 Found {len(rows)} reports in database")

        requests = []
        for row in rows:
            requests.append({
                "id": row[0],
                "patient_id": row[1],
                "patient_name": row[2],
                "test_name": row[3],
                "status": row[4],
                "requested_at": str(row[5]) if row[5] else "N/A",
                "upload_date": str(row[6]) if row[6] else None,
                "s3_key": row[7]
            })

        # Print first few for debugging
        for i, req in enumerate(requests[:3]):
            print(f"  {i+1}. {req['patient_name']} - {req['test_name']} - {req['status']}")

        return jsonify(requests)

    except Exception as e:
        print(f"❌ Error in get_report_requests: {e}")
        import traceback
        traceback.print_exc()
        return jsonify([])
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

@app.route('/reports/<int:patient_id>', methods=['GET'])
def get_reports(patient_id):
    """
    Fetch all reports for a patient.
    If report is uploaded, return presigned download URL.
    """
    conn = None
    cur = None

    try:
        conn = get_db_connection()
        if not conn:
            return jsonify([]), 500

        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        cur.execute("""
            SELECT
                id,
                test_name,
                status,
                filename,
                s3_key,
                upload_date,
                requested_at
            FROM reports
            WHERE patient_id = %s
            ORDER BY
                upload_date DESC NULLS LAST,
                requested_at DESC
        """, (patient_id,))

        rows = cur.fetchall()
        reports = []

        for row in rows:
            status = row['status'] or 'Pending'
            download_url = None

            # Generate download URL ONLY if uploaded
            if status == 'Uploaded' and row['s3_key']:
                try:
                    download_url = s3_client.generate_presigned_url(
                        'get_object',
                        Params={
                            'Bucket': BUCKET,
                            'Key': row['s3_key']
                        },
                        ExpiresIn=3600
                    )
                except Exception as e:
                    print("❌ Presigned URL error:", e)
                    download_url = None

            reports.append({
                "report_id": row['id'],
                "test_name": row['test_name'],
                "status": status,
                "filename": row['filename'],
                "requested_at": str(row['requested_at']) if row['requested_at'] else None,
                "upload_date": str(row['upload_date']) if row['upload_date'] else None,
                "download_url": download_url
            })

        return jsonify(reports)

    except Exception as e:
        print("❌ Error fetching reports:", e)
        return jsonify([]), 500

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()




# ---------- ADMIN ----------
@app.route('/admin/login', methods=['POST'])
def admin_login():
    data = request.json
    conn = get_db_connection()
    if not conn:
        return jsonify({"success": False, "error": "Database down"}), 500

    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM admins WHERE username=%s AND password=%s",
            (data['username'], data['password'])
        )
        admin = cur.fetchone()
        return jsonify({"success": admin is not None})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        cur.close()
        conn.close()

@app.route('/admin/appointments')
def get_appointments():
    conn = get_db_connection()
    if not conn:
        return jsonify([]), 500

    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT a.id, a.patient_id, p.name, a.appointment_date, a.treatment, a.status
            FROM appointments a
            LEFT JOIN patients p ON a.patient_id = p.id
            ORDER BY a.appointment_date DESC
        """)

        appointments = []
        for row in cur.fetchall():
            appointments.append({
                "id": row[0],
                "patient_id": row[1],
                "patient_name": row[2] or f"Patient {row[1]}",
                "appointment_date": str(row[3]),
                "treatment": row[4],
                "status": row[5]
            })

        return jsonify(appointments)
    except Exception as e:
        print(f"Appointments error: {e}")
        return jsonify([])
    finally:
        cur.close()
        conn.close()

@app.route('/generate-upload-url', methods=['POST'])
def generate_upload_url():
    data = request.json
    patient_id = data.get('patient_id')
    test_name = data.get('test_name')

    # Generate S3 key with proper extension
    import uuid
    from werkzeug.utils import secure_filename

    # Create proper filename
    safe_test_name = secure_filename(test_name)
    unique_id = uuid.uuid4().hex[:8]
    filename = f"{safe_test_name}_{unique_id}.pdf"
    s3_key = f"reports/{patient_id}/{filename}"

    # Generate presigned URL for upload
    upload_url = s3_client.generate_presigned_url(
        'put_object',
        Params={
            'Bucket': 'dental-clinic-reports-1',
            'Key': s3_key,
            'ContentType': 'application/pdf'
        },
        ExpiresIn=3600
    )

    return jsonify({
        'success': True,
        'upload_url': upload_url,
        's3_key': s3_key  # <-- THIS IS CRITICAL!
    })

@app.route('/upload-report', methods=['POST'])
def upload_report():
    try:
        data = request.get_json()
        print("📥 Upload-report payload:", data)

        if not data or 'report_id' not in data:
            return jsonify({
                'success': False,
                'error': 'report_id missing in request'
            }), 400

        report_id = data['report_id']
        filename = data.get('filename')
        s3_key = data.get('s3_key')

        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            UPDATE reports
            SET filename = %s,
                s3_key = %s,
                status = 'Uploaded',
                upload_date = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (filename, s3_key, report_id))

        if cur.rowcount == 0:
            conn.rollback()
            return jsonify({
                'success': False,
                'error': 'Report ID not found'
            }), 404

        conn.commit()
        cur.close()
        conn.close()

        return jsonify({
            'success': True,
            'message': 'Report uploaded successfully',
            'report_id': report_id
        })

    except Exception as e:
        print("❌ Upload-report exception:", e)
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500



@app.route('/admin/update-appointment-status', methods=['POST'])
def update_appointment_status():
    """Update appointment status (Complete/Cancel/Booked)"""
    data = request.json
    print(f"📋 Updating appointment {data.get('appointment_id')} to {data.get('status')}")

    conn = get_db_connection()
    if not conn:
        return jsonify({"success": False, "error": "Database down"}), 500

    try:
        cur = conn.cursor()

        cur.execute(
            "UPDATE appointments SET status = %s WHERE id = %s RETURNING id",
            (data['status'], data['appointment_id'])
        )

        updated = cur.fetchone()
        conn.commit()

        if updated:
            print(f"✅ Appointment {data['appointment_id']} updated to {data['status']}")
            return jsonify({
                "success": True,
                "message": f"Appointment marked as {data['status']}"
            })
        else:
            return jsonify({"success": False, "error": "Appointment not found"}), 404

    except Exception as e:
        conn.rollback()
        print(f"❌ Error updating appointment: {e}")
        return jsonify({"success": False, "error": str(e)}), 400
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

# ---------- HEALTH ----------
@app.route('/health')
def health():
    return jsonify({"status": "ok"})

@app.route('/health/db')
def health_db():
    conn = get_db_connection()
    if conn:
        conn.close()
        return jsonify({"database": "connected"})
    return jsonify({"database": "disconnected"}), 500

@app.route('/health/s3')
def health_s3():
    try:
        s3_client.list_buckets()
        return jsonify({"s3": "connected"})
    except Exception as e:
        return jsonify({"s3": "error", "details": str(e)}), 500


# ---------- RUN ----------
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port, debug=True)