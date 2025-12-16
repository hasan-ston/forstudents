from threading import Thread
import os
import json
import smtplib
import uuid
from datetime import datetime
from email.message import EmailMessage
from functools import wraps
import requests

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from flask_jwt_extended import (
    JWTManager,
    create_access_token,
    get_jwt_identity,
    jwt_required,
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

try:
    import stripe
except ImportError:  # pragma: no cover - stripe not installed in some envs
    stripe = None
try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
except ImportError:  # pragma: no cover - optional
    boto3 = None
    BotoCoreError = ClientError = Exception

from sqlalchemy.exc import OperationalError

# Basic Flask setup
app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-key")
app.config["JWT_SECRET_KEY"] = os.getenv("JWT_SECRET_KEY", "dev-jwt-secret")
_raw_db_url = os.getenv("DATABASE_URL", "sqlite:///app.db")
# If using Postgres without explicit driver, switch to psycopg (psycopg3) driver
if _raw_db_url.startswith("postgres://"):
    _raw_db_url = _raw_db_url.replace("postgres://", "postgresql+psycopg://", 1)
elif _raw_db_url.startswith("postgresql://") and "+psycopg" not in _raw_db_url:
    _raw_db_url = _raw_db_url.replace("postgresql://", "postgresql+psycopg://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = _raw_db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_pre_ping": True,
    "pool_recycle": 300,
    "pool_size": int(os.getenv("DB_POOL_SIZE", "5")),
    "max_overflow": int(os.getenv("DB_MAX_OVERFLOW", "2")),
    "pool_timeout": 30,
}
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # 25MB uploads
app.config["UPLOAD_FOLDER"] = os.path.join(app.instance_path, "uploads")

os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

db = SQLAlchemy(app)
jwt = JWTManager(app)
CORS(app, resources={r"/api/*": {"origins": "*"}})

# Payments / plan settings
FREE_DOC_LIMIT = int(os.getenv("FREE_DOC_LIMIT", "2"))
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_PRICE_ID = os.getenv("STRIPE_PRICE_ID", "")
STRIPE_CONNECT_ACCOUNT_ID = os.getenv("STRIPE_CONNECT_ACCOUNT_ID", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
SIMULATE_PAYMENTS = os.getenv("SIMULATE_PAYMENTS", "true").lower() == "true"
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", SMTP_USER or "no-reply@example.com")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "mhd.hasan236@gmail.com")
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "")
SENDGRID_FROM = os.getenv("SENDGRID_FROM", EMAIL_FROM)
S3_BUCKET = os.getenv("S3_BUCKET", "")
AWS_REGION = os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", ""))
DOWNLOAD_URL_EXPIRY = int(os.getenv("DOWNLOAD_URL_EXPIRY", "300"))

s3_client = None
if boto3 and S3_BUCKET:
    s3_client = boto3.client("s3", region_name=AWS_REGION or None)

if stripe and STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY


# Models
class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(32), default="user")  # user | admin
    subscription_status = db.Column(db.String(32), default="free")  # free | paid
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    accesses = db.relationship(
        "DocumentAccess", backref="user", lazy="dynamic", cascade="all, delete-orphan"
    )

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def serialize(self):
        used = self.accesses.count()
        remaining = max(FREE_DOC_LIMIT - used, 0)
        return {
            "id": self.id,
            "email": self.email,
            "role": self.role,
            "subscription_status": self.subscription_status,
            "free_docs_remaining": remaining,
            "accessed_doc_ids": [a.document_id for a in self.accesses],
        }


class Document(db.Model):
    __tablename__ = "documents"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False)
    course_code = db.Column(db.String(64), nullable=False)
    year = db.Column(db.String(16), nullable=True)
    term = db.Column(db.String(32), nullable=True)
    kind = db.Column(db.String(32), nullable=False)  # paper | solution
    notes = db.Column(db.String(255), nullable=True)
    file_name = db.Column(db.String(255), nullable=False)
    storage = db.Column(db.String(16), default="local")  # local | s3
    s3_key = db.Column(db.String(512), nullable=True)
    content_type = db.Column(db.String(128), default="application/pdf")
    status = db.Column(db.String(32), default="pending")  # pending | approved | rejected
    uploader_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    uploader = db.relationship("User", backref="documents")

    def serialize(self, include_status=False):
        data = {
            "id": self.id,
            "title": self.title,
            "course_code": self.course_code,
            "year": self.year,
            "term": self.term,
            "kind": self.kind,
            "notes": self.notes,
            "status": self.status if include_status else None,
            "uploader": {"id": self.uploader.id, "email": self.uploader.email},
            "created_at": self.created_at.isoformat(),
        }
        return data


class DocumentAccess(db.Model):
    __tablename__ = "document_accesses"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    document_id = db.Column(db.Integer, db.ForeignKey("documents.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    document = db.relationship("Document")

    __table_args__ = (db.UniqueConstraint("user_id", "document_id", name="uniq_user_doc"),)


class Feedback(db.Model):
    __tablename__ = "feedback"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    document_id = db.Column(db.Integer, db.ForeignKey("documents.id"), nullable=True)
    message = db.Column(db.String(1000), nullable=False)
    contact = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User")
    document = db.relationship("Document")

    def serialize(self):
        return {
            "id": self.id,
            "message": self.message,
            "contact": self.contact,
            "document_id": self.document_id,
            "user_email": self.user.email if self.user else None,
            "created_at": self.created_at.isoformat(),
        }


class DownloadAudit(db.Model):
    __tablename__ = "download_audits"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    document_id = db.Column(db.Integer, db.ForeignKey("documents.id"), nullable=False)
    ip_address = db.Column(db.String(64))
    user_agent = db.Column(db.String(512))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


with app.app_context():
    db.create_all()


# Helpers
def admin_required(fn):
    @wraps(fn)
    @jwt_required()
    def wrapper(*args, **kwargs):
        user = _current_user()
        if not user:
            return jsonify({"error": "Unauthorized"}), 401
        if user.role != "admin":
            return jsonify({"error": "Admin only"}), 403
        return fn(*args, **kwargs)

    return wrapper


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in {"pdf"}


def _current_user() -> User:
    user_id = get_jwt_identity()
    if not user_id:
        return None
    try:
        return User.query.get(int(user_id))
    except Exception:
        return None


def _use_s3() -> bool:
    return bool(s3_client and S3_BUCKET)


def send_email(to_address: str, subject: str, body: str) -> bool:
    """Best-effort mailer: tries SendGrid API first, then SMTP."""
    if not to_address:
        return False

    # Prefer SendGrid API (avoids SMTP port egress issues on some hosts)
    if SENDGRID_API_KEY:
        try:
            payload = {
                "personalizations": [{"to": [{"email": to_address}]}],
                "from": {"email": SENDGRID_FROM},
                "subject": subject,
                "content": [{"type": "text/plain", "value": body}],
            }
            resp = requests.post(
                "https://api.sendgrid.com/v3/mail/send",
                headers={
                    "Authorization": f"Bearer {SENDGRID_API_KEY}",
                    "Content-Type": "application/json",
                },
                data=json.dumps(payload),
                timeout=10,
            )
            if 200 <= resp.status_code < 300:
                return True
            print(f"Email send failed (SendGrid) to {to_address}: {resp.status_code} {resp.text}")
        except Exception as e:
            print(f"Email send failed (SendGrid) to {to_address}: {e}")

    # Fallback to SMTP if configured
    if SMTP_HOST and SMTP_USER and SMTP_PASS:
        try:
            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"] = EMAIL_FROM
            msg["To"] = to_address
            msg.set_content(body)

            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
                server.starttls()
                server.login(SMTP_USER, SMTP_PASS)
                server.send_message(msg)
            return True
        except Exception as e:
            print(f"Email send failed (SMTP) to {to_address}: {e}")

    return False


# Auth endpoints
@app.post("/api/auth/register")
def register():
    data = request.get_json() or {}
    email, password = data.get("email"), data.get("password")
    if not email or not password:
        return jsonify({"error": "Email and password required"}), 400
    if User.query.filter_by(email=email).first():
        return jsonify({"error": "User already exists"}), 409

    # Only promote to admin when email matches configured admin email.
    role = "admin" if (os.getenv("ADMIN_EMAIL") and email == os.getenv("ADMIN_EMAIL")) else "user"

    user = User(email=email, role=role)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()

    token = create_access_token(identity=str(user.id))
    return jsonify({"access_token": token, "user": user.serialize()}), 201


@app.post("/api/auth/login")
def login():
    data = request.get_json() or {}
    email, password = data.get("email"), data.get("password")
    if not email or not password:
        return jsonify({"error": "Email and password required"}), 400

    user = User.query.filter_by(email=email).first()
    if not user or not user.check_password(password):
        return jsonify({"error": "Invalid credentials"}), 401

    token = create_access_token(identity=str(user.id))
    return jsonify({"access_token": token, "user": user.serialize()})


@app.get("/api/me")
@jwt_required()
def me():
    user = _current_user()
    return jsonify({"user": user.serialize(), "free_limit": FREE_DOC_LIMIT})


# Document endpoints
@app.post("/api/docs")
@jwt_required()
def upload_doc():
    user = _current_user()
    if "file" not in request.files:
        return jsonify({"error": "PDF file required"}), 400

    file = request.files["file"]
    title = request.form.get("title")
    course_code = request.form.get("course_code")
    year = request.form.get("year")
    term = request.form.get("term")
    kind = request.form.get("kind", "paper")
    notes = request.form.get("notes")

    if not file or file.filename == "":
        return jsonify({"error": "PDF file required"}), 400
    if not allowed_file(file.filename):
        return jsonify({"error": "Only PDF uploads are allowed"}), 400
    if not title or not course_code:
        return jsonify({"error": "Title and course code required"}), 400
    if kind not in {"paper", "solution"}:
        return jsonify({"error": "Invalid document type"}), 400

    filename = f"{uuid.uuid4().hex}_{secure_filename(file.filename)}"
    content_type = file.mimetype or "application/pdf"
    storage = "local"
    s3_key = None

    if _use_s3():
        s3_key = f"uploads/{filename}"
        try:
            file.stream.seek(0)
            s3_client.upload_fileobj(
                Fileobj=file.stream,
                Bucket=S3_BUCKET,
                Key=s3_key,
                ExtraArgs={"ContentType": content_type, "ACL": "private"},
            )
            storage = "s3"
        except (BotoCoreError, ClientError) as exc:
            return jsonify({"error": f"S3 upload failed: {exc}"}), 502
    else:
        filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        file.save(filepath)

    doc = Document(
        title=title,
        course_code=course_code,
        year=year,
        term=term,
        kind=kind,
        notes=notes,
        file_name=filename,
        storage=storage,
        s3_key=s3_key,
        content_type=content_type,
        status="pending",
        uploader_id=user.id,
    )
    db.session.add(doc)
    db.session.commit()

    # Notify admin and uploader (best effort)
    send_email(
        ADMIN_EMAIL,
        "New past paper awaiting approval",
        f"Title: {doc.title}\nCourse: {doc.course_code}\nType: {doc.kind}\nUploader: {user.email}\nDoc ID: {doc.id}",
    )
    send_email(
        user.email,
        "Thanks for your submission",
        f"We received your upload '{doc.title}'. The admin will review it soon.",
    )

    return jsonify({"document": doc.serialize(include_status=True)}), 201


@app.get("/api/docs")
@jwt_required(optional=True)
def list_docs():
    user = None
    try:
        user = _current_user()
    except Exception:
        user = None

    base_query = Document.query.order_by(Document.created_at.desc())
    if user and user.role == "admin":
        status = request.args.get("status")
        if status:
            base_query = base_query.filter_by(status=status)
    else:
        base_query = base_query.filter_by(status="approved")
    docs = base_query.limit(200).all()

    include_status = bool(user and user.role == "admin")
    serialized = [d.serialize(include_status=include_status) for d in docs]
    return jsonify({"documents": serialized})


@app.post("/api/docs/<int:doc_id>/approve")
@admin_required
def approve_doc(doc_id: int):
    doc = Document.query.get_or_404(doc_id)
    doc.status = "approved"
    db.session.commit()
    return jsonify({"document": doc.serialize(include_status=True)})


@app.post("/api/docs/<int:doc_id>/reject")
@admin_required
def reject_doc(doc_id: int):
    doc = Document.query.get_or_404(doc_id)
    doc.status = "rejected"
    db.session.commit()
    return jsonify({"document": doc.serialize(include_status=True)})


@app.delete("/api/docs/<int:doc_id>")
@admin_required
def delete_doc(doc_id: int):
    doc = Document.query.get_or_404(doc_id)
    # Remove file from storage
    if doc.storage == "s3" and _use_s3() and doc.s3_key:
        try:
            s3_client.delete_object(Bucket=S3_BUCKET, Key=doc.s3_key)
        except Exception:
            pass
    else:
        try:
            filepath = os.path.join(app.config["UPLOAD_FOLDER"], doc.file_name)
            if os.path.isfile(filepath):
                os.remove(filepath)
        except Exception:
            pass

    # Delete related records (best effort)
    try:
        DownloadAudit.query.filter_by(document_id=doc.id).delete(synchronize_session=False)
        DocumentAccess.query.filter_by(document_id=doc.id).delete(synchronize_session=False)
    except Exception:
        db.session.rollback()
    try:
        db.session.delete(doc)
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"error": "Failed to delete document"}), 500
    return jsonify({"deleted": True, "document_id": doc_id})


@app.get("/api/docs/<int:doc_id>/download")
@jwt_required()
def download_doc(doc_id: int):
    user = _current_user()
    doc = Document.query.get_or_404(doc_id)

    if doc.status != "approved" and user.role != "admin":
        return jsonify({"error": "Document not available"}), 403

    # Enforce free plan quota
    accessed = {a.document_id for a in user.accesses}
    if user.role != "admin" and user.subscription_status != "paid":
        if doc.id not in accessed and len(accessed) >= FREE_DOC_LIMIT:
            return jsonify({"error": "Upgrade required to access more documents"}), 402

    if doc.id not in accessed:
        db.session.add(DocumentAccess(user_id=user.id, document_id=doc.id))
        db.session.commit()

    # Log download attempt (best effort)
    try:
        ip_forwarded = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        ip_addr = ip_forwarded or request.remote_addr
        ua = request.headers.get("User-Agent")
        audit = DownloadAudit(
            user_id=user.id if user else None,
            document_id=doc.id,
            ip_address=ip_addr,
            user_agent=ua,
        )
        db.session.add(audit)
        db.session.commit()
    except Exception:
        db.session.rollback()

    # Serve from S3 with short-lived presigned URL by default
    if doc.storage == "s3" and _use_s3() and doc.s3_key:
        try:
            url = s3_client.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": S3_BUCKET,
                    "Key": doc.s3_key,
                    "ResponseContentDisposition": f'attachment; filename="{doc.file_name}"',
                    "ResponseContentType": doc.content_type or "application/pdf",
                },
                ExpiresIn=DOWNLOAD_URL_EXPIRY,
            )
            return jsonify({"download_url": url})
        except (BotoCoreError, ClientError) as exc:
            return jsonify({"error": f"File missing on server: {exc}"}), 410
        except Exception as exc:
            return jsonify({"error": f"Cannot generate download link: {exc}"}), 500

    filepath = os.path.join(app.config["UPLOAD_FOLDER"], doc.file_name)
    if not os.path.isfile(filepath):
        return jsonify({"error": "File missing on server"}), 410

    return send_from_directory(
        app.config["UPLOAD_FOLDER"],
        doc.file_name,
        as_attachment=True,
        mimetype=doc.content_type or "application/pdf",
    )


# Feedback
@app.post("/api/feedback")
@jwt_required(optional=True)
def submit_feedback():
    data = request.get_json() or {}
    message = data.get("message")
    contact = data.get("contact")
    document_id = data.get("document_id")
    if not message:
        return jsonify({"error": "Message required"}), 400
    current_user = _current_user()
    fb = Feedback(
        message=message,
        contact=contact,
        document_id=document_id,
        user_id=current_user.id if current_user else None,
    )
    db.session.add(fb)
    try:
        db.session.commit()
    except OperationalError:
        db.session.rollback()
        return jsonify({"error": "Database connection issue, please retry"}), 503
    except Exception:
        db.session.rollback()
        return jsonify({"error": "Failed to save feedback"}), 500

    # Send emails asynchronously (non-blocking)
    user_email = current_user.email if current_user else contact

    def send_feedback_emails():
        try:
            send_email(
                ADMIN_EMAIL,
                "New feedback received",
                f"From: {user_email or 'anonymous'}\nMessage: {message}\nDocument ID: {document_id}",
            )
            ack_to = current_user.email if current_user else (contact if contact and "@" in contact else None)
            if ack_to:
                send_email(ack_to, "We received your feedback", "Thanks for sharing feedback. We'll review it shortly.")
        except Exception as e:
            print(f"Failed to send feedback email: {e}")

    Thread(target=send_feedback_emails, daemon=True).start()

    return jsonify({"feedback": fb.serialize()}), 201


# Billing
@app.post("/api/billing/checkout")
@jwt_required()
def start_checkout():
    user = _current_user()

    if stripe and STRIPE_SECRET_KEY and STRIPE_PRICE_ID:
        try:
            stripe_kwargs = {}
            if STRIPE_CONNECT_ACCOUNT_ID:
                stripe_kwargs["stripe_account"] = STRIPE_CONNECT_ACCOUNT_ID

            session = stripe.checkout.Session.create(
                mode="subscription",
                line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}],
                success_url=f"{FRONTEND_URL}/billing/success?session_id={{CHECKOUT_SESSION_ID}}",
                cancel_url=f"{FRONTEND_URL}/billing/cancel",
                customer_email=user.email,
                metadata={"user_id": user.id},
                **stripe_kwargs,
            )
            return jsonify({"checkout_url": session.url})
        except Exception as exc:  # pragma: no cover - depends on stripe
            return jsonify({"error": f"Stripe error: {exc}"}), 502

    if SIMULATE_PAYMENTS:
        user.subscription_status = "paid"
        db.session.commit()
        return jsonify({"simulated": True, "subscription_status": user.subscription_status})

    return jsonify({"error": "Payments not configured"}), 503


@app.post("/api/billing/webhook")
def billing_webhook():
    if not stripe or not STRIPE_WEBHOOK_SECRET:
        return jsonify({"error": "Webhook not configured"}), 503

    payload = request.data
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except Exception as exc:  # pragma: no cover - depends on stripe
        return jsonify({"error": f"Webhook error: {exc}"}), 400

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        user_id = session.get("metadata", {}).get("user_id")
        if user_id:
            user = User.query.get(int(user_id))
            if user:
                user.subscription_status = "paid"
                db.session.commit()
    elif event["type"] == "customer.subscription.deleted":
        subscription = event["data"]["object"]
        user_id = subscription.get("metadata", {}).get("user_id")
        if user_id:
            user = User.query.get(int(user_id))
            if user:
                user.subscription_status = "free"
                db.session.commit()

    return jsonify({"received": True})


@app.get("/healthz")
def health():
    return jsonify({"status": "ok"})


@app.get("/")
def root():
    return jsonify({"message": "Backend running", "docs_endpoint": "/api/docs"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
