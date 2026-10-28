"""Quick manual check of paypal_client.py against the real PayPal sandbox."""
import uuid

import paypal_client as pc

key = str(uuid.uuid4())

created = pc.create_order("10.00", "USD", key)
print("CREATE:", created)

replayed = pc.create_order("10.00", "USD", key)
print("Same order on replay?", created.order_id == replayed.order_id)

if created.ok:
    print("GET:", pc.get_order(created.order_id))