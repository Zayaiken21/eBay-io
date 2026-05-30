from fastapi import FastAPI, Request, Query
from fastapi.responses import JSONResponse
import hashlib
import hmac
import os

app = FastAPI()

VERIFICATION_TOKEN = os.getenv("EBAY_VERIFICATION_TOKEN")
ENDPOINT_URL = os.getenv("EBAY_ENDPOINT_URL")


@app.get("/ebay/account-deletion")
async def validate_endpoint(challenge_code: str = Query(...)):
    """
    eBay calls this when you save the endpoint.
    You must return a SHA-256 challenge response.
    """
    raw = challenge_code + VERIFICATION_TOKEN + ENDPOINT_URL
    challenge_response = hashlib.sha256(raw.encode("utf-8")).hexdigest()

    return {
        "challengeResponse": challenge_response
    }


@app.post("/ebay/account-deletion")
async def receive_account_deletion(request: Request):
    """
    eBay sends account deletion / closure notifications here.
    """
    payload = await request.json()

    print("Received eBay deletion notification:")
    print(payload)

    # Example: extract user identifiers if present
    user_id = payload.get("metadata", {}).get("userId") or payload.get("userId")

    if user_id:
        # TODO: delete this user's saved data from your DB
        # delete_user_data(user_id)
        print(f"Delete local data for eBay user: {user_id}")

    return JSONResponse(status_code=200, content={"status": "received"})