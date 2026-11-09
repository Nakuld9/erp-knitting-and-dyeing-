"""Creating ledger accounts from the API (chart-of-accounts management)."""
from __future__ import annotations


def test_create_posting_account_under_a_group(client, auth_headers):
    r = client.post("/api/accounts", headers=auth_headers, json={
        "code": "6099", "name": "Miscellaneous Office Expenses",
        "account_type": "EXPENSE", "parent_code": "6000"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["code"] == "6099" and body["allow_posting"] is True and body["is_group"] is False

    # it now appears in the list and is postable from a voucher
    codes = [a["code"] for a in client.get("/api/accounts", headers=auth_headers).json()]
    assert "6099" in codes


def test_create_group_heading(client, auth_headers):
    r = client.post("/api/accounts", headers=auth_headers, json={
        "code": "6500", "name": "Selling & Distribution", "account_type": "EXPENSE",
        "is_group": True})
    assert r.status_code == 201
    assert r.json()["is_group"] is True and r.json()["allow_posting"] is False


def test_duplicate_code_rejected(client, auth_headers):
    r = client.post("/api/accounts", headers=auth_headers, json={
        "code": "6010", "name": "Dup", "account_type": "EXPENSE", "parent_code": "6000"})
    assert r.status_code == 409


def test_invalid_type_rejected(client, auth_headers):
    r = client.post("/api/accounts", headers=auth_headers, json={
        "code": "9001", "name": "x", "account_type": "BOGUS"})
    assert r.status_code == 422


def test_parent_must_be_a_group(client, auth_headers):
    r = client.post("/api/accounts", headers=auth_headers, json={
        "code": "9002", "name": "x", "account_type": "EXPENSE", "parent_code": "6010"})
    assert r.status_code == 422 and "not a group" in r.text


def test_type_must_match_parent(client, auth_headers):
    # 6000 is an EXPENSE group; a LIABILITY child is rejected
    r = client.post("/api/accounts", headers=auth_headers, json={
        "code": "9003", "name": "x", "account_type": "LIABILITY", "parent_code": "6000"})
    assert r.status_code == 422


def test_new_loan_account_then_post_to_it(client, auth_headers):
    # add a specific term-loan account under the Long-term Liabilities group (2500)
    r = client.post("/api/accounts", headers=auth_headers, json={
        "code": "2511", "name": "HDFC Term Loan A/c 1234", "account_type": "LIABILITY",
        "parent_code": "2500"})
    assert r.status_code == 201, r.text
    # a balanced journal touching it posts (Dr Bank 1020 / Cr the new loan) — proves it is postable
    v = client.post("/api/vouchers", headers=auth_headers, json={
        "voucher_type_code": "JV", "voucher_date": "2026-11-09", "posting_reference": "JV-LOAN-1",
        "narration": "Loan drawdown", "lines": [
            {"account_code": "1020", "debit": "500000", "credit": "0"},
            {"account_code": "2511", "debit": "0", "credit": "500000"}]})
    assert v.status_code == 201, v.text
