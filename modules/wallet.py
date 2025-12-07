from eth_account.messages import (
    encode_defunct,
    encode_typed_data,
    _hash_eip191_message
)
from web3.auto import w3

from modules.database import DataBase


class Wallet:
    def __init__(
            self,
            privatekey: str,
            encoded_pk: str,
            db: DataBase,
    ):
        self.privatekey = privatekey
        self.encoded_pk = encoded_pk
        self.db = db

        self.account = w3.eth.account.from_key(privatekey) if privatekey else None
        self.address = self.account.address if privatekey else None


    def sign_message(
            self,
            text: str = None,
            typed_data: dict = None,
            hash: bool = False
    ):
        if text:
            message = encode_defunct(text=text)
        elif typed_data:
            message = encode_typed_data(full_message=typed_data)
            if hash:
                message = encode_defunct(hexstr=_hash_eip191_message(message).hex())

        signed_message = self.account.sign_message(message)
        signature = signed_message.signature.hex()
        if not signature.startswith('0x'): signature = '0x' + signature
        return signature
