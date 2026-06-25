import asyncio
from sqlalchemy import select
from app.core.database import get_async_session
from app.models.bank_connection import BankConnection
from app.models.account import Account
from app.models.transaction import Transaction

async def main():
    async for session in get_async_session():
        # Query connections
        conn_res = await session.execute(select(BankConnection))
        conns = conn_res.scalars().all()
        print(f"--- CONNECTIONS (Total: {len(conns)}) ---")
        for c in conns:
            print(f"ID: {c.id}, Provider: {c.provider}, External ID: {c.external_id}, Status: {c.status}, Inst: {c.institution_name}")

        # Query accounts
        acc_res = await session.execute(select(Account))
        accs = acc_res.scalars().all()
        print(f"\n--- ACCOUNTS (Total: {len(accs)}) ---")
        for a in accs:
            print(f"ID: {a.id}, Conn ID: {a.connection_id}, Name: {a.name}, Type: {a.type}, Balance: {a.balance} {a.currency}")

        # Query transactions
        tx_res = await session.execute(select(Transaction).limit(5))
        txs = tx_res.scalars().all()
        print(f"\n--- TRANSACTIONS (Total: {len(txs)} shown) ---")
        for t in txs:
            print(f"ID: {t.id}, Desc: {t.description}, Amount: {t.amount} {t.currency}, Date: {t.date}")
        break

if __name__ == "__main__":
    asyncio.run(main())
