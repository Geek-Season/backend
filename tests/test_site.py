"""Настройки сайта: дата ЕГЭ для счётчика на главной."""


def test_exam_date(client, admin_client, login):
    assert client.get("/site").json() == {"exam_date": None}  # не задана — счётчика нет

    student = login()
    assert client.put("/admin/site", json={"exam_date": "2027-06-08"}, headers=student).status_code == 403
    assert client.put("/admin/site", json={"exam_date": "2027-06-08"}).status_code == 401

    assert admin_client.put("/admin/site", json={"exam_date": "2027-06-08"}).json() == {"exam_date": "2027-06-08"}
    assert client.get("/site").json() == {"exam_date": "2027-06-08"}  # гостю тоже
    text = admin_client.get("/admin/actions").json()["items"][0]["text"]
    assert text == "изменил дату ЕГЭ: — → 08.06.2027"

    admin_client.put("/admin/site", json={"exam_date": "2027-06-08"})  # то же самое — в журнал не пишем
    assert len(admin_client.get("/admin/actions").json()["items"]) == 1

    assert admin_client.put("/admin/site", json={"exam_date": None}).json() == {"exam_date": None}
    assert admin_client.get("/admin/actions").json()["items"][0]["text"] == "убрал дату ЕГЭ"
    assert admin_client.put("/admin/site", json={"exam_date": "завтра"}).status_code == 422
