from __future__ import annotations

from ..utils.http_client import http_client
from ..utils.login_utils import hostloc_prepare_cookie, render_set_cookie


class HostLocLogic:
    @staticmethod
    async def login(username: str, password: str) -> str:
        forum = await http_client.get("https://hostloc.com/forum.php", headers={"ignore": "true"})
        cookie = render_set_cookie(forum) + hostloc_prepare_cookie(forum.text)
        response = await http_client.post(
            "https://hostloc.com/member.php?mod=logging&action=login&loginsubmit=yes&infloat=yes&lssubmit=yes&inajax=1",
            data={
                "fastloginfield": "username",
                "username": username,
                "cookietime": "2592000",
                "password": password,
                "quickforward": "yes",
                "handlekey": "ls",
            },
            headers={"Referer": "https://hostloc.com/forum.php", "Cookie": cookie},
        )
        extra = hostloc_prepare_cookie(response.text)
        if extra:
            response = await http_client.post(
                "https://hostloc.com/member.php?mod=logging&action=login&loginsubmit=yes&infloat=yes&lssubmit=yes&inajax=1",
                data={
                    "fastloginfield": "username",
                    "username": username,
                    "cookietime": "2592000",
                    "password": password,
                    "quickforward": "yes",
                    "handlekey": "ls",
                },
                headers={"Referer": "https://hostloc.com/forum.php", "Cookie": cookie + extra},
            )
        if "https://hostloc.com/forum.php" in response.text:
            return render_set_cookie(response)
        raise RuntimeError("账号或密码错误或其他原因登录失败！")
