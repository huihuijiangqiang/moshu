"""Authentication endpoint tests."""

from api.auth import hash_password, verify_password


def test_password_hash_is_salted_and_verifiable():
    first = hash_password("correct horse battery staple")
    second = hash_password("correct horse battery staple")

    assert first != second
    assert verify_password("correct horse battery staple", first) is True
    assert verify_password("wrong password", first) is False


async def test_register_login_refresh_and_me(app_client):
    register = await app_client.post(
        "/auth/register",
        json={"name": "新作者", "email": "Author@Example.Test", "password": "long-enough-password"},
    )

    assert register.status_code == 201
    registered = register.json()
    assert registered["user"]["email"] == "author@example.test"
    assert registered["access_token"] != registered["refresh_token"]

    duplicate = await app_client.post(
        "/auth/register",
        json={"name": "另一个名字", "email": "author@example.test", "password": "long-enough-password"},
    )
    assert duplicate.status_code == 409

    invalid = await app_client.post(
        "/auth/login",
        json={"email": "author@example.test", "password": "not-the-password"},
    )
    assert invalid.status_code == 401

    login = await app_client.post(
        "/auth/login",
        json={"email": "author@example.test", "password": "long-enough-password"},
    )
    assert login.status_code == 200
    session = login.json()

    me = await app_client.get("/auth/me", headers={"Authorization": f"Bearer {session['access_token']}"})
    assert me.status_code == 200
    assert me.json()["name"] == "新作者"

    refresh = await app_client.post("/auth/refresh", json={"refresh_token": session["refresh_token"]})
    assert refresh.status_code == 200
    assert refresh.json()["access_token"] != session["access_token"]


async def test_refresh_token_cannot_call_access_endpoint(app_client):
    register = await app_client.post(
        "/auth/register",
        json={"name": "作者", "email": "refresh@example.test", "password": "long-enough-password"},
    )
    token = register.json()["refresh_token"]

    response = await app_client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
