"""Password login with revocable, expiring server-side sessions."""
import hashlib
import hmac
import secrets
import threading
import time

COOKIE = "docker_monitor_session"
SESSION_SECONDS = 12 * 60 * 60


class Auth:
    def __init__(self, password, username="admin"):
        self.password = password
        self.username = username
        self.sessions = {}
        self.attempts = {}
        self.lock = threading.RLock()

    @property
    def enabled(self):
        return bool(self.password)

    def authenticated(self, token):
        if not self.enabled:
            return True
        if not token:
            return False
        key = hashlib.sha256(token.encode()).hexdigest()
        with self.lock:
            return self.sessions.get(key, 0) > time.time()

    def login(self, username, password, peer):
        now = time.time()
        with self.lock:
            self.attempts = {k: v for k, v in self.attempts.items() if v[0] > now - 300}
            started, count = self.attempts.get(peer, (now, 0))
            if count >= 10:
                return None, 429
            valid_user = hmac.compare_digest(username.encode(), self.username.encode())
            valid_password = hmac.compare_digest(password.encode(), self.password.encode())
            if not (valid_user and valid_password):
                self.attempts[peer] = (started, count + 1)
                return None, 401
            self.attempts.pop(peer, None)
            self.sessions = {k: v for k, v in self.sessions.items() if v > now}
            token = secrets.token_urlsafe(32)
            self.sessions[hashlib.sha256(token.encode()).hexdigest()] = now + SESSION_SECONDS
            return token, 200

    def logout(self, token):
        if token:
            with self.lock:
                self.sessions.pop(hashlib.sha256(token.encode()).hexdigest(), None)
