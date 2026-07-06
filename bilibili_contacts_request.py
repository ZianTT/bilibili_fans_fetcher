import base64
import gzip
import hashlib
from pathlib import Path
import sys
import time
import qrcode
import httpx
import urllib


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "gen"))

import contacts_pb2


URL = "https://app.bilibili.com/bilibili.app.im.v1.im/Contacts"


# AUTHORIZATION = (
#     "identify_v1 "
#     "15c168cafc2f5c0832c4d3dd3bfb4b71CjCJxmNydrE3t4FachueFFj3w5VkY4CBVnWBMe10-"
#     "2FHiOlDU14sT1Y2NCzkDhUaZvYSVjZXcFJ4cVh3VVZGcWlNcWlUR0VXeW56YkRoWGR4YS1rQ2"
#     "Q0UHNwdjdfTU1nZHFpMkpraEpsQzNCV2FPNVdLNUMyUVF4ellNRjJqV0NCWjRVWWJSdlh3IIEC"
# )

AUTHORIZATION = ""

USER_AGENT = (
    "Dalvik/2.1.0 (Linux; U; Android 12; V2304A Build/W528JS) "
    "8.88.0 os/android model/V2304A mobi_app/android build/8880300 "
    "channel/bili innerVer/8880310 osVer/12 network/2"
)

def appsign(params, appkey, appsec):
    params.update({'appkey': appkey})
    params = dict(sorted(params.items())) # 按照 key 重排参数
    query = urllib.parse.urlencode(params) # 序列化参数
    sign = hashlib.md5((query+appsec).encode()).hexdigest() # 计算 api 签名
    params.update({'sign':sign})
    return params



def appkey_login():
    # https://passport.bilibili.com/x/passport-tv-login/qrcode/auth_code
    # POST local_id:0 appkey:4409e2ce8ffd12b8 ts:0 sign:e134154ed6add881d28fbdf68653cd9c
    resp = httpx.post(
        "https://passport.bilibili.com/x/passport-tv-login/qrcode/auth_code",
        headers={
            "user-agent": USER_AGENT,
        },
        data={
            "local_id": "0",
            "appkey": "4409e2ce8ffd12b8",
            "ts": "0",
            "sign": "e134154ed6add881d28fbdf68653cd9c",
        },
    )
    print(resp.json())
    code = resp.json().get("data", {}).get("auth_code")
    url = resp.json().get("data", {}).get("url")
    qr = qrcode.QRCode()
    qr.add_data(url)
    qr.print_ascii(invert=True)
    while True:
        appkey = '4409e2ce8ffd12b8'
        appsec = '59b43e04ad6965f34319062b478f83dd'
        params = {
            "ts": 0,
            "auth_code": code,
            "local_id": "0"
        }
        signed_params = appsign(params, appkey, appsec)
        resp = httpx.post(
            "https://passport.bilibili.com/x/passport-tv-login/qrcode/poll",
            headers={
                "user-agent": USER_AGENT,
            },
            data=signed_params
        )
        print(resp.json())
        if resp.json().get("code", -1) == 0:
            global AUTHORIZATION
            AUTHORIZATION = "identify_v1 " + resp.json().get("data", {}).get("access_token")
            break
        time.sleep(1)
    print("OK, Logined as mid", resp.json().get("data", {}).get("mid"), " Press ENTER to continue...")
    input()

# appkey_login()

def grpc_body(pb):
    data = pb.SerializeToString()
    return b"\x00" + len(data).to_bytes(4, "big") + data


def build_headers():
    return {
        "content-type": "application/grpc",
        "authorization": AUTHORIZATION,
        "user-agent": USER_AGENT,
    }


def parse_grpc_response(resp):
    data = resp.content
    pos = 0
    while pos + 5 <= len(data):
        compressed = data[pos]
        size = int.from_bytes(data[pos + 1 : pos + 5], "big")
        frame = data[pos + 5 : pos + 5 + size]
        pos += 5 + size

        if compressed:
            encoding = resp.headers.get("grpc-encoding", "")
            if encoding == "gzip":
                frame = gzip.decompress(frame)
            else:
                print("unknown grpc compression:", encoding)
                print("frame hex:", frame.hex())
                continue

        msg = contacts_pb2.ContactsReply()
        msg.ParseFromString(frame)
        return msg

    if pos != len(data):
        print("unparsed tail hex:", data[pos:].hex())

contact_list = []

def main(pagination_params=None):
    if pagination_params is None:
        req = contacts_pb2.ContactsReq(
            tab=contacts_pb2.ContactTabType.TAB_FANS,
        )
    else:
        req = contacts_pb2.ContactsReq(
            tab=contacts_pb2.ContactTabType.TAB_FANS,
            pagination_params=pagination_params,
        )
    body = grpc_body(req)

    with httpx.Client(http2=True, timeout=10) as client:
        resp = client.post(URL, headers=build_headers(), content=body)

    if resp.content:
        content_type = resp.headers.get("content-type", "")
        if "application/grpc" in content_type:
            for contact in parse_grpc_response(resp).contacts:
                contact_list.append({
                    "name": contact.name,
                    "id": contact.id,
                    "official_type": contact.official_type,
                })
        else:
            print(resp.text)
    else:
        print("empty response body")
        grpc_message = resp.headers.get("grpc-message", "")
        if grpc_message == "-101":
            print("invalid authorization, please update AUTHORIZATION")
            appkey_login()
            main()
            return
        else:
            print("grpc-message:", grpc_message)
            return
        
    
    if parse_grpc_response(resp).pagination_params.has_more:
        print("has more contacts to fetch, current:", len(contact_list))
        main(parse_grpc_response(resp).pagination_params)
    else:
        print("all contacts fetched, total:", len(contact_list))
        open("contacts.csv", "w", encoding="utf-8").write(
            "name,id,official_type\n" +
            "\n".join(f"{c['name']},{c['id']},{c['official_type']}" for c in contact_list)
        )
        print("contacts.csv saved, done.")


if __name__ == "__main__":
    main()
