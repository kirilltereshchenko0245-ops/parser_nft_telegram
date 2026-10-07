"""Crypto Pay API integration for TON plans."""
import aiohttp
import logging

log = logging.getLogger('giftparser.payments')

API_URL = 'https://pay.crypt.bot/api'


class CryptoPay:
    def __init__(self, token: str | None):
        self.token = (token or '').strip()
        self.session = None

    @property
    def configured(self):
        return bool(self.token)

    async def _session(self):
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        return self.session

    async def call(self, method: str, **params):
        if not self.configured:
            raise RuntimeError('CRYPTO_PAY_API_TOKEN не настроен')
        session = await self._session()
        headers = {'Crypto-Pay-API-Token': self.token}
        async with session.post(f'{API_URL}/{method}', json=params, headers=headers) as r:
            data = await r.json(content_type=None)
            if r.status != 200 or not data.get('ok'):
                raise RuntimeError(str(data.get('error') or data)[:500])
            return data['result']

    async def create_invoice(self, amount: str, description: str, payload: str, expires_in: int = 3600):
        return await self.call(
            'createInvoice', asset='TON', amount=str(amount), description=description,
            payload=payload, allow_comments=False, allow_anonymous=False,
            expires_in=int(expires_in),
        )

    async def get_invoice(self, invoice_id: int):
        rows = await self.call('getInvoices', invoice_ids=str(int(invoice_id)), count=1)
        return rows[0] if rows else None

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()
