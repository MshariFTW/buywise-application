"""Interactive, optional removal of selected headphone records from BuyWise."""
import psycopg

from buywise_db import connect_db, password_prompt


def remove_selected(conn, selected):
    ids = list(selected)
    current = dict(conn.execute(
        "SELECT id, title FROM products WHERE category='headphones' AND id=ANY(%s) FOR UPDATE",
        (ids,),
    ).fetchall())
    if current != selected:
        raise ValueError("The selected records changed. Run the preview again.")
    conn.execute(
        """DELETE FROM price_observations h USING offers o
           WHERE h.offer_id=o.id AND o.product_id=ANY(%s)""", (ids,)
    )
    conn.execute("DELETE FROM offers WHERE product_id=ANY(%s)", (ids,))
    conn.execute("DELETE FROM product_image_embeddings WHERE product_id=ANY(%s)", (ids,))
    conn.execute("DELETE FROM buywise_offer_scans WHERE product_id=ANY(%s)", (ids,))
    conn.execute("DELETE FROM buywise_saved_items WHERE product_id=ANY(%s)", (ids,))
    conn.execute(
        """UPDATE discovery_cache SET product_ids=ARRAY(
              SELECT id FROM unnest(product_ids) WITH ORDINALITY AS p(id, n)
              WHERE NOT (id=ANY(%s)) ORDER BY n)
           WHERE product_ids && %s::bigint[]""", (ids, ids)
    )
    return conn.execute(
        "DELETE FROM products WHERE category='headphones' AND id=ANY(%s)", (ids,)
    ).rowcount


def main():
    password = password_prompt()
    with connect_db(password) as conn:
        headphones = dict(conn.execute(
            "SELECT id, title FROM products WHERE category='headphones' ORDER BY id"
        ).fetchall())
        print("Database:", conn.info.dbname)
    if not headphones:
        print("No products in the headphones category.")
        return
    for pid, title in headphones.items():
        print(f"{pid}  {title}")
    choice = input("Enter one product ID or ALL for every headphone shown (Enter to cancel): ").strip()
    if choice == "ALL":
        selected = headphones
    elif choice.isascii() and choice.isdecimal() and int(choice) in headphones:
        pid = int(choice)
        selected = {pid: headphones[pid]}
    else:
        print("Cancelled.")
        return
    print(f"Selected {len(selected)} headphone(s), including their offers, price history and saved notes.")
    if input("Type DELETE to permanently remove these records: ").strip() != "DELETE":
        print("Cancelled.")
        return
    with connect_db(password) as conn:
        removed = remove_selected(conn, selected)
    print(f"Deleted {removed} headphone(s).")


if __name__ == "__main__":
    try:
        main()
    except (psycopg.Error, ValueError) as exc:
        print(f"Cleanup stopped. No changes were committed. {exc}")
        raise SystemExit(1)
