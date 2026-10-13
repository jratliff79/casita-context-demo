"""Domain-separated readiness challenge proofs; the shared secret is never sent."""
import hashlib
import hmac
import json


def proof(secret, domain, value):
    encoded = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
    return hmac.new(secret.encode(), domain + b'\0' + encoded, hashlib.sha256).hexdigest()


def request_proof(secret, body):
    return proof(secret, b'relay-readiness.request.v1', body)


def response_proof(secret, workspace, nonce):
    return proof(secret, b'relay-readiness.response.v1',
                 {'workspace': workspace, 'nonce': nonce, 'ready': True})
