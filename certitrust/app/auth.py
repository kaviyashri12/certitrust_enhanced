from functools import wraps
from flask import session, redirect, url_for, request

# Simple hardcoded admin for prototype
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "sona@123"

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("main.admin_login", next=request.url))
        return f(*args, **kwargs)
    return decorated