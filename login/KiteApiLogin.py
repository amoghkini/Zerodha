import logging, pyotp, kiteconnect.exceptions as ex
from kiteconnect import KiteConnect

logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger(__name__)


class Kite(KiteConnect):
    KITE_API_DEV_LOGIN = "https://kite.trade/connect/login"

    @staticmethod
    def totp(totp_secret: str) -> str:
        if len(totp_secret) != 32:
            raise ValueError("Incorrect TOTP BASE32 Secret Key")
        return pyotp.TOTP(totp_secret).now().zfill(6)

    def __init__(self, api_key: str, *args, **kwargs) -> None:
        KiteConnect.__init__(self, api_key=api_key, *args, **kwargs)
        self._routes.update(
            {
                "api.login": "/api/login",
                "api.twofa": "/api/twofa",
            }
        )

    def login_and_generate_session(
        self,
        user_id: str,
        password: str,
        totp_secret: str,
        api_secret: str,
    ) -> None:
        resp = self._request(
            "api.login",
            "POST",
            params={"user_id": user_id, "password": password},
        )
        if resp.get("request_id") is not None:
            request_id = resp["request_id"]
            log.debug(f"request_id = {request_id}")
        else:
            raise ex.GeneralException("Request id is not found", code=resp.status_code)
        resp = self._request(
            "api.twofa",
            "POST",
            params={
                "user_id": user_id,
                "request_id": request_id,
                "twofa_value": self.totp(totp_secret),
            },
        )
        try:
            resp = self.reqsession.get(
                self.KITE_API_DEV_LOGIN,
                params={"api_key": self.api_key},
            )
            resp.raise_for_status()
        except requests.exceptions.HTTPError as errh:
            log.error(f"Http Error: {errh}")
            raise ex.GeneralException(f"Http Error: {errh}", code=resp.status_code)
        except requests.exceptions.ConnectionError as errc:
            log.error(f"Error Connecting: {errc}")
            raise ex.GeneralException(
                f"Error Connecting: {errc}", code=resp.status_code
            )
        except requests.exceptions.Timeout as errt:
            log.error(f"Timeout Error: {errt}")
            raise ex.GeneralException(f"Timeout Error: {errt}", code=resp.status_code)
        except requests.exceptions.RequestException as err:
            log.error(f"Some Other Error: {err}")
            raise ex.GeneralException(f"Some Other Error: {err}", code=resp.status_code)
        else:
            request_token = resp.url.split("request_token=")
            if len(request_token) < 2:
                raise ex.GeneralException(
                    "Failed To Find Request Token In The Redirected URL"
                )
            request_token = request_token[1].split("&")[0]
            log.debug(f"request_token = {request_token}")
            self.reqsession.cookies.clear()
            access_token = self.generate_session(
                request_token,
                api_secret=api_secret,
            )
            if access_token.get("access_token") is not None:
                access_token = access_token["access_token"]
                self.set_access_token(access_token)
                log.debug(f"access_token = {access_token}")
            else:
                raise ex.GeneralException(
                    f"Failed To Generate Kite Session: {access_token}"
                )


if __name__ == "__main__":
    # Replace your credentials in the below variables
    user_id = "YOUR_USER_ID"
    password = "YOUR_PASSWORD"
    totp_secret = "YOUR_BASE32_TOTP_SECRET"
    api_key = "YOUR_API_KEY"
    api_secret = "YOUR_API_SECRET"

    kite = Kite(api_key)
    kite.login_and_generate_session(
        user_id,
        password,
        totp_secret,
        api_secret,
    )
    log.debug(
        f"Profile For The Logged In User Is {kite.profile()}",
    )
