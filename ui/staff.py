"""Staff and Admin screens: dashboard, donations, stock, households,
hampers, counter, tasks and reports (F-01 to F-05, F-07).

Screens only call services through ``system()`` and never read files
(NFR-18). Pickup and counter screens identify households by code only
(NFR-22).
"""

from __future__ import annotations

from datetime import timedelta

import streamlit as st

from foodshare.errors import NotFoundError
from foodshare.models import TaskKind
from foodshare.services.clients import CONTACT_METHODS, OPTIONAL_CATEGORIES, OPTIONS
from foodshare.services.stock import FLAG_REASONS, STORAGE_TYPES
from foodshare.services.tasks import ISSUE_TYPES

from .common import attempt, flash, show_flash, succeeded, system, table, user

CATEGORY_LABELS = {"dietary": "Dietary needs", "religious": "Religious or cultural food needs",
                   "medical": "Medical dietary needs", "allergies": "Allergies",
                   "accessibility": "Access needs"}


# =============================================================== dashboard
def dashboard_page():
    st.title("Today at a glance")
    show_flash()
    s, u = system(), user()
    today = s.clock.today()
    expiring = attempt(s.stock.expiring_soon, u) or []
    low = attempt(s.stock.low_stock_alerts, u) or []
    flags = attempt(s.tasks.open_flags, u) or []
    pickups = [p for p in (attempt(s.hampers.list_pickups, u, today) or []) if p.status.value == "booked"]

    cols = st.columns(4)
    cols[0].metric("Expiring soon", len(expiring))
    cols[1].metric("Low-stock alerts", len(low))
    cols[2].metric("Issues from volunteers", len(flags))
    cols[3].metric("Pickups still to come today", len(pickups))

    st.subheader("Use these first (expiring soon)")
    table([{"Batch": b.id, "Item": b.item, "Available": f"{b.available:g} {b.unit}",
            "Use by": b.expiry, "Storage": b.storage} for b in expiring],
          "Nothing expires in the next few days.")
    st.subheader("Low stock (essential items)")
    table([{"Item": r["item"], "Available": f"{r['available']:g} {r['unit']}",
            "Minimum": f"{r['min_level']:g}"} for r in low], "No essential item is running low.")
    st.subheader("Issues raised by volunteers")
    table([{"Task": f["task"], "Issue": f["issue_type"], "Note": f["note"], "Raised by": f["raised_by"],
            "When": f["raised_at"][11:16]} for f in flags], "No open issues.")


# =============================================================== donations
def donations_page():
    st.title("Donations")
    show_flash()
    s, u = system(), user()
    record_tab, offer_tab = st.tabs(["Record a donation", "Donor offer"])

    with record_tab:
        items = [i.id for i in s.stock.list_items()]
        cart = st.session_state.setdefault("donation_cart", [])
        with st.form("donation_line", clear_on_submit=True):
            st.markdown("**Add an item** (UC-01)")
            c1, c2 = st.columns(2)
            item = c1.selectbox("Item", items)
            qty = c2.number_input("Quantity", min_value=0.0, value=1.0, step=1.0)
            c3, c4 = st.columns(2)
            expiry = c3.date_input("Use-by / best-before date", value=s.clock.today() + timedelta(days=30))
            storage = c4.selectbox("Storage", STORAGE_TYPES)
            if st.form_submit_button("Add item to this donation"):
                cart.append({"item": item, "quantity": qty, "expiry": expiry.isoformat(), "storage": storage})
                st.rerun()
        if cart:
            st.markdown("**Items in this donation**")
            table([{"Item": c["item"], "Quantity": c["quantity"], "Use by": c["expiry"],
                    "Storage": c["storage"]} for c in cart])
            with st.form("donation_save"):
                st.caption("Donor details are optional. Leave them blank for an anonymous donation.")
                donor = st.text_input("Donor name (optional)")
                contact = st.text_input("Donor contact (optional)")
                c1, c2 = st.columns(2)
                save = c1.form_submit_button("Save donation", type="primary")
                clear = c2.form_submit_button("Start again")
            if clear:
                st.session_state["donation_cart"] = []
                st.rerun()
            if save:
                donation = attempt(s.stock.record_donation, u, cart, donor, contact)
                if donation:
                    st.session_state["donation_cart"] = []
                    st.session_state["last_confirmation"] = s.stock.confirmation_text(donation)
                    flash(f"Donation {donation.id} saved and added to stock.")
        confirmation = st.session_state.get("last_confirmation")
        if confirmation:
            st.markdown("**Confirmation for the donor** (FR-05)")
            st.code(confirmation, language=None)
            st.download_button("Download confirmation", confirmation, file_name="donation_confirmation.txt")

    with offer_tab:
        needs = s.stock.priority_needs()
        st.info("**We currently need:** " + ", ".join(needs["needs"]) + "\n\n" + needs["guidance"])
        with st.form("offer", clear_on_submit=True):
            donor = st.text_input("Donor name or organisation")
            c1, c2, c3 = st.columns(3)
            item = c1.text_input("Item offered")
            qty = c2.number_input("Quantity", min_value=0.0, value=1.0, step=1.0)
            expiry = c3.date_input("Expiry date", value=s.clock.today() + timedelta(days=7))
            drop = st.text_input("Preferred drop-off time (e.g. Friday 8 am)")
            if st.form_submit_button("Save offer", type="primary"):
                offer = attempt(s.stock.record_offer, u, donor,
                                [{"item": item, "quantity": qty, "expiry": expiry.isoformat()}], drop)
                if offer:
                    flash(f"Offer {offer.id} saved.")
        offers = attempt(s.stock.list_offers, u) or []
        st.markdown("**Recent offers**")
        table([{"Ref": o.id, "Donor": o.donor_name, "Items": ", ".join(f"{i['item']} x{i['quantity']:g}" for i in o.items),
                "Drop-off": o.dropoff_time, "Recorded": o.recorded_on} for o in offers], "No offers yet.")


# =================================================================== stock
def stock_page():
    st.title("Stock")
    show_flash()
    s, u = system(), user()
    summary_tab, expiry_tab, flag_tab, setup_tab = st.tabs(
        ["Stock levels", "Expiring soon", "Flag a problem", "Items and alerts"])

    with summary_tab:
        rows = attempt(s.stock.stock_summary, u) or []
        table([{"Category": r["category"], "Item": r["item"], "On hand": f"{r['on_hand']:g} {r['unit']}",
                "Free to allocate": f"{r['available']:g} {r['unit']}", "Nearest use-by": r["nearest_expiry"] or "-",
                "Low": "Yes" if r["essential"] and r["available"] < r["min_level"] else ""} for r in rows])
        with st.expander("Show every batch"):
            batches = attempt(s.stock.list_batches, u) or []
            table([{"Batch": b.id, "Item": b.item, "On hand": b.quantity, "Reserved": b.reserved,
                    "Unit": b.unit, "Use by": b.expiry, "Storage": b.storage} for b in batches])

    with expiry_tab:
        days = st.number_input("Show batches expiring within (days)", min_value=0, max_value=90,
                               value=int(s.settings()["expiry_threshold_days"]), step=1)
        batches = attempt(s.stock.expiring_soon, u, int(days)) or []
        table([{"Batch": b.id, "Item": b.item, "Available": f"{b.available:g} {b.unit}", "Use by": b.expiry}
               for b in batches], "Nothing expires in that period.")
        if st.button("Make this the default"):
            if succeeded(s.stock.set_expiry_threshold, u, int(days)):
                flash(f"Default changed to {int(days)} days.")

    with flag_tab:
        flag_form(prefix="staff")
        st.markdown("**Flagged batches**")
        table([{"Batch": b.id, "Item": b.item, "Reason": b.flag_reason, "By": b.flagged_by,
                "When": b.flagged_at[:16]} for b in (attempt(s.stock.flagged_batches, u) or [])],
              "No flagged batches.")

    with setup_tab:
        st.markdown("**Essential items and minimum levels** (FR-11)")
        items = s.stock.list_items()
        with st.form("essential"):
            name = st.selectbox("Item", [i.id for i in items])
            current = next(i for i in items if i.id == name) if items else None
            essential = st.checkbox("Essential item (show a low-stock alert)", value=bool(current and current.essential))
            minimum = st.number_input("Alert when available stock falls below", min_value=0.0,
                                      value=float(current.min_level if current else 0), step=1.0)
            if st.form_submit_button("Save alert setting"):
                if attempt(s.stock.set_essential, u, name, essential, minimum):
                    flash(f"Alert setting saved for {name}.")
        st.markdown("**Priority needs shown to donors** (FR-03)")
        with st.form("needs"):
            text = st.text_input("Comma-separated list", ", ".join(s.stock.priority_needs()["needs"]))
            if st.form_submit_button("Save priority needs"):
                if succeeded(s.stock.set_priority_needs, u, text.split(",")):
                    flash("Priority needs saved.")
        st.markdown("**Add an item to the catalogue**")
        with st.form("new_item", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            name = c1.text_input("Item name")
            category = c2.selectbox("Category", [r.id for r in (attempt(s.hampers.list_rules, u) or [])])
            unit = c3.text_input("Unit (must match the category's rule)")
            allergens = st.multiselect("Contains these allergens", OPTIONS["allergies"])
            tags = st.multiselect("Suitable for", OPTIONS["dietary"] + OPTIONS["religious"] + OPTIONS["medical"])
            if st.form_submit_button("Add item"):
                if attempt(s.stock.add_item, u, name, category, unit, allergens, tags):
                    flash(f"{name} added to the catalogue.")


def flag_form(prefix: str):
    """Flag a damaged, expired or missing batch (FR-12, UC-03). Shared with volunteers."""
    s, u = system(), user()
    batches = attempt(s.stock.list_batches, u) or []
    free = [b for b in batches if b.reserved == 0]
    if not free:
        st.info("There are no batches to flag.")
        return
    labels = {f"{b.id} - {b.item} ({b.quantity:g} {b.unit}, use by {b.expiry})": b.id for b in free}
    with st.form(f"{prefix}_flag", clear_on_submit=True):
        choice = st.selectbox("Batch", list(labels))
        reason = st.radio("What is wrong?", FLAG_REASONS, horizontal=True)
        note = st.text_input("Short note (optional), e.g. 'dented can'")
        if st.form_submit_button("Flag and remove from stock", type="primary"):
            if attempt(s.stock.flag_batch, u, labels[choice], reason, note):
                flash("Thank you. The batch has been removed from available stock.")


# ============================================================== households
def households_page():
    st.title("Households")
    show_flash()
    s, u = system(), user()
    find_tab, new_tab = st.tabs(["Find a household", "New household"])

    with find_tab:
        search = st.text_input("Search by preferred name or ID")
        rows = attempt(s.clients.list_clients, u, search) or []
        if not rows:
            st.info("No matching households.")
        else:
            labels = {f"{r['id']} - {r['preferred_name']} (household of {r['household_size']})": r["id"] for r in rows}
            choice = st.selectbox("Choose a household", list(labels))
            if st.button("Open profile"):
                st.session_state["open_client"] = labels[choice]
                st.rerun()
        client_id = st.session_state.get("open_client")
        if client_id:
            client_profile(client_id)

    with new_tab:
        st.caption("Collect only what is needed. Each optional question explains why we ask.")
        data = profile_form("new", None)
        if data is not None:
            client = attempt(s.clients.create_client, u, data)
            if client:
                st.session_state["open_client"] = client.id
                flash(f"Household {client.id} created. Open the 'Find a household' tab to book a pickup.")


def profile_form(key: str, client):
    """Profile fields with a consent question per sensitive category (FR-13 to FR-16)."""
    with st.form(f"profile_{key}"):
        c1, c2 = st.columns(2)
        name = c1.text_input("Name the client would like us to use", value=client.preferred_name if client else "")
        language = c2.text_input("Preferred language", value=client.preferred_language if client else "English")
        c1, c2 = st.columns(2)
        method = c1.selectbox("How should we contact them?", CONTACT_METHODS,
                              index=CONTACT_METHODS.index(client.contact_method) if client else 0)
        detail = c2.text_input("Phone number or email (not needed for 'In person')",
                               value=client.contact_detail if client else "")
        st.markdown("**Household**")
        c = st.columns(4)
        adults = c[0].number_input("Adults", 0, 20, client.adults if client else 1)
        children = c[1].number_input("Children", 0, 20, client.children if client else 0)
        infants = c[2].number_input("Infants", 0, 20, client.infants if client else 0)
        older = c[3].number_input("Older members", 0, 20, client.older if client else 0)
        pickup_pref = st.text_input("Pickup preference (e.g. after 4 pm)", value=client.pickup_preference if client else "")
        st.markdown("**Optional needs** - ask, explain why, and record the answer")
        data_needs, consents = {}, {}
        for category in OPTIONAL_CATEGORIES:
            with st.container(border=True):
                st.markdown(f"**{CATEGORY_LABELS[category]}**")
                st.caption(system().clients.consent_purpose(category))
                agreed = st.checkbox("The client agrees to us recording this", key=f"{key}_{category}_ok",
                                     value=bool(client and client.consents.get(category)))
                values = st.multiselect("Choose any that apply", OPTIONS[category], key=f"{key}_{category}",
                                        default=getattr(client, category) if client else [])
                consents[category] = agreed
                data_needs[category] = values
        other = st.text_input("Other needs (optional)", value=client.other_needs_note if client else "")
        pin = st.text_input("Kiosk PIN, 4 numbers (optional; leave blank to keep)", type="password", max_chars=4)
        submitted = st.form_submit_button("Save household", type="primary")
    if not submitted:
        return None
    data = {"preferred_name": name, "preferred_language": language, "contact_method": method,
            "contact_detail": detail, "adults": adults, "children": children, "infants": infants,
            "older": older, "pickup_preference": pickup_pref, "consents": consents,
            "other_needs_note": other, **data_needs}
    if pin:
        data["kiosk_pin"] = pin
    return data


def client_profile(client_id: str):
    s, u = system(), user()
    cache_key = f"profile_cache_{client_id}"
    if cache_key not in st.session_state:          # audit one "view" per opening (FR-35)
        st.session_state[cache_key] = attempt(s.clients.get_client, u, client_id)
    client = st.session_state[cache_key]
    if client is None:
        st.session_state.pop("open_client", None)
        return
    st.divider()
    top = st.columns([4, 1])
    top[0].subheader(f"{client.preferred_name} ({client.id})")
    if top[1].button("Close profile"):
        st.session_state.pop("open_client", None)
        st.session_state.pop(cache_key, None)
        st.rerun()
    tags = client.requirement_tags() + [f"{a}-free" for a in client.allergies]
    if tags:
        st.markdown(" ".join(f"<span class='tag'>{tag}</span>" for tag in tags), unsafe_allow_html=True)

    pickup_tab, edit_tab, notes_tab, rights_tab = st.tabs(["Pickups", "Edit profile", "Notes and visits", "Data rights"])
    with pickup_tab:
        with st.form("book"):
            c1, c2 = st.columns(2)
            day = c1.date_input("Pickup date", value=s.clock.today())
            slot = c2.selectbox("Time slot", s.hampers.pickup_slots())
            if st.form_submit_button("Book pickup", type="primary"):
                pickup = attempt(s.hampers.book_pickup, u, client.id, day, slot)
                if pickup:
                    flash(f"Pickup booked. Code: {pickup.code}")
        pickups = attempt(s.hampers.client_pickups, u, client.id) or []
        table([{"Code": p.code, "Date": p.date, "Slot": p.slot, "Status": p.status.value} for p in pickups],
              "No pickups booked.")
        booked = [p for p in pickups if p.status.value == "booked"]
        if booked:
            chosen = st.selectbox("Message text for pickup", [p.code for p in booked])
            pickup = next(p for p in booked if p.code == chosen)
            message = attempt(s.hampers.pickup_message, u, pickup.id)
            if message:
                st.caption(f"Send by {message['method']} ({message['language']}). "
                           "It contains only the time and code, in case the phone is shared.")
                st.code(message["text"], language=None)

    with edit_tab:
        data = profile_form(f"edit_{client.id}", client)
        if data is not None:
            updated = attempt(s.clients.update_client, u, client.id, data)
            if updated:
                st.session_state[cache_key] = updated
                flash("Profile updated.")

    with notes_tab:
        for note in client.notes:
            label = "Restricted - staff only" if note.restricted else "Note"
            st.markdown(f"**{label}** ({note.author}, {note.written_at[:10]}): {note.text}")
        with st.form("note", clear_on_submit=True):
            text = st.text_area("Add a note")
            restricted = st.checkbox("Restricted: only staff and admins can see it")
            if st.form_submit_button("Save note"):
                if attempt(s.clients.add_note, u, client.id, text, restricted):
                    st.session_state.pop(cache_key, None)
                    flash("Note saved.")
        st.markdown("**Visit history** (staff and admins only)")
        table([{"Date": v.date, "Hamper": v.hamper_id} for v in client.visits], "No visits yet.")

    with rights_tab:
        st.caption("Use these when a client asks to see, correct or delete their information "
                   "(Australian Privacy Principles 12 and 13). Corrections are made in 'Edit profile'.")
        if st.button("Show client information"):
            summary = attempt(s.clients.information_summary, u, client.id)
            if summary:
                st.session_state["summary_text"] = summary
        if st.session_state.get("summary_text"):
            st.code(st.session_state["summary_text"], language=None)
            st.download_button("Download a printable copy", st.session_state["summary_text"],
                               file_name=f"{client.id}_information.txt")
        with st.form("delete"):
            st.warning("Deleting removes every personal detail. Only de-identified visit counts are kept.")
            sure = st.checkbox("The client has asked us to delete their information")
            if st.form_submit_button("Delete personal information"):
                if not sure:
                    st.error("Tick the box to confirm the client's request first.")
                elif attempt(s.clients.delete_client, u, client.id):
                    st.session_state.pop("open_client", None)
                    st.session_state.pop(cache_key, None)
                    st.session_state.pop("summary_text", None)
                    flash("Personal information deleted.")


# ================================================================= hampers
def hampers_page():
    st.title("Hampers")
    show_flash()
    s, u = system(), user()
    st.caption("Households are shown by pickup code only.")
    today = s.clock.today()
    pickups = attempt(s.hampers.list_pickups, u) or []
    upcoming = [p for p in pickups if p.status.value == "booked" and p.date >= today.isoformat()]
    table([{"Code": p.code, "Date": p.date, "Slot": p.slot,
            "Hamper": p.hamper_id or "not generated"} for p in upcoming], "No upcoming pickups.")

    code = st.text_input("Pickup code", key="hamper_code").strip().upper()
    if not code:
        st.info("Enter a pickup code to generate or review its hamper.")
    else:
        try:
            hamper = s.hampers.hamper_for_code(u, code)
        except NotFoundError as exc:
            st.error(str(exc))
        else:
            if hamper is None:
                if st.button("Generate suggested hamper", type="primary"):
                    if attempt(s.hampers.generate_hamper, u, code):
                        flash("Suggested hamper ready. Check it, then confirm.")
            else:
                show_hamper(hamper)

    st.divider()
    st.subheader("Hampers not collected")
    open_hampers = attempt(s.hampers.list_hampers, u, ["confirmed", "packed"]) or []
    due = [h for h in open_hampers if s.hampers.pickup_code_for(h)]
    table([{"Hamper": h.id, "Code": s.hampers.pickup_code_for(h), "Status": h.status.value} for h in due],
          "Every hamper has been collected.")
    if due:
        with st.form("return"):
            hid = st.selectbox("Hamper to return to stock (pickup day over)", [h.id for h in due])
            if st.form_submit_button("Return items to stock"):
                if attempt(s.hampers.return_uncollected, u, hid):
                    flash(f"{hid} returned. Its items are back in available stock.")


def show_hamper(hamper):
    s, u = system(), user()
    st.markdown(f"### Hamper {hamper.id} - {hamper.status.value}")
    table([{"#": i, "Item": l.item, "Quantity": f"{l.quantity:g} {l.unit}", "Use-by batch": l.batch_id,
            "Rule used": l.rule_text, "Packed": "Yes" if l.packed else "",
            "Substitution note": l.substitution_note} for i, l in enumerate(hamper.lines)], "No items.")
    if hamper.shortfalls:
        st.warning("Could not be supplied (recorded as unmet needs when confirmed): " +
                   "; ".join(f"{g['category']} {g['quantity']:g} {g['unit']} ({g['need']})" for g in hamper.shortfalls))
    for a in hamper.adjustments:
        st.caption(f"Changed {a['item']} from {a['from']:g} to {a['to']:g} by {a['by']}: {a['reason']}")

    if hamper.status.value == "suggested":
        if hamper.lines:
            with st.form("adjust"):
                st.markdown("**Change a quantity** (a reason is required and recorded)")
                c1, c2 = st.columns(2)
                index = c1.selectbox("Line", list(range(len(hamper.lines))),
                                     format_func=lambda i: f"{i}: {hamper.lines[i].item}")
                qty = c2.number_input("New quantity (0 removes it)", min_value=0.0, value=0.0, step=1.0)
                reason = st.text_input("Reason")
                if st.form_submit_button("Apply change"):
                    if attempt(s.hampers.adjust_line, u, hamper.id, index, qty, reason):
                        flash("Change saved and recorded in the audit log.")
        c1, c2, c3 = st.columns(3)
        priority = c1.selectbox("Packing priority", [1, 2, 3], index=1,
                                format_func=lambda p: {1: "High", 2: "Normal", 3: "Low"}[p])
        if c2.button("Confirm and create packing task", type="primary"):
            task = attempt(s.hampers.confirm_hamper, u, hamper.id, priority)
            if task:
                flash(f"Confirmed. Packing task {task.id} created; assign it on the Tasks page.")
        if c3.button("Cancel hamper"):
            if attempt(s.hampers.cancel_hamper, u, hamper.id):
                flash("Hamper cancelled.")
    elif hamper.status.value == "confirmed":
        st.info("Waiting to be packed. Assign the packing task on the Tasks page.")
        if st.button("Cancel hamper and release stock"):
            if attempt(s.hampers.cancel_hamper, u, hamper.id):
                flash("Hamper cancelled and stock released.")
    elif hamper.status.value == "packed":
        st.success("Packed and ready for collection at the counter.")


# ================================================================= counter
def counter_page():
    """Pickup counter: code in, collection out. No names on screen (NFR-22, H5)."""
    st.title("Pickup counter")
    show_flash()
    s, u = system(), user()
    with st.form("counter", clear_on_submit=True):
        code = st.text_input("Ask for the pickup code and type it here")
        look = st.form_submit_button("Find", type="primary")
    if look and code.strip():
        st.session_state["counter_code"] = code.strip().upper()
    code = st.session_state.get("counter_code")
    if not code:
        return
    view = attempt(s.hampers.counter_lookup, u, code)
    if view is None:
        st.session_state.pop("counter_code", None)
        return
    st.markdown(f"<div class='big-code'>{view['code']}</div>", unsafe_allow_html=True)
    st.write(f"{view['date']}, {view['slot']}  |  Hamper: {view['hamper_status']}  |  Items: {view['item_count']}")
    c1, c2 = st.columns(2)
    if c1.button("Confirm collection", type="primary", disabled=view["hamper_status"] != "packed"):
        done = attempt(s.hampers.confirm_collection, u, code)
        if done:
            st.session_state.pop("counter_code", None)
            flash(f"Collection of {done['code']} recorded. Thank you.")
    if c2.button("Clear"):
        st.session_state.pop("counter_code", None)
        st.rerun()


# =================================================================== tasks
def tasks_page():
    st.title("Volunteer tasks")
    show_flash()
    s, u = system(), user()
    volunteers = attempt(s.tasks.list_volunteers, u) or []
    with st.form("new_task", clear_on_submit=True):
        st.markdown("**New task**")
        c1, c2, c3 = st.columns(3)
        kind = c1.selectbox("Type", [k.value for k in TaskKind if k != TaskKind.PACK])
        assignee = c2.selectbox("Assign to", ["(leave open)"] + volunteers)
        priority = c3.selectbox("Priority", [1, 2, 3], index=1,
                                format_func=lambda p: {1: "High", 2: "Normal", 3: "Low"}[p])
        title = st.text_input("What needs doing?")
        if st.form_submit_button("Create task", type="primary"):
            who = "" if assignee == "(leave open)" else assignee
            if attempt(s.tasks.create_task, u, kind, title, who, priority):
                flash("Task created.")

    tasks = attempt(s.tasks.all_tasks, u) or []
    st.markdown("**Tasks to do**")
    table([{"Task": t.id, "Title": t.title, "Priority": t.priority, "Assigned to": t.assigned_to or "open",
            "Status": t.status.value} for t in tasks], "No tasks waiting.")
    if tasks:
        with st.form("assign"):
            c1, c2 = st.columns(2)
            task_id = c1.selectbox("Task", [t.id for t in tasks])
            who = c2.selectbox("Assign to", ["(leave open)"] + volunteers)
            if st.form_submit_button("Assign"):
                if attempt(s.tasks.assign_task, u, task_id, "" if who == "(leave open)" else who):
                    flash(f"{task_id} updated.")

    flags = attempt(s.tasks.open_flags, u) or []
    st.markdown("**Issues raised**")
    table([{"Task": f["task_id"], "Issue": f["issue_type"], "Note": f["note"], "By": f["raised_by"]} for f in flags],
          "No open issues.")
    if flags:
        labels = {f"{f['task_id']} - {f['issue_type']}: {f['note']}": f for f in flags}
        choice = st.selectbox("Issue", list(labels))
        if st.button("Mark issue resolved"):
            f = labels[choice]
            if succeeded(s.tasks.resolve_flag, u, f["task_id"], f["index"]):
                flash("Issue resolved.")


# ================================================================= reports
def reports_page():
    st.title("Reports")
    st.caption("Reports contain totals only - never names, contact details or pickup codes.")
    s, u = system(), user()
    with st.form("report_form"):
        kind = st.radio("Report", ["Stock movement", "Demand and unmet needs"], horizontal=True)
        c1, c2 = st.columns(2)
        start = c1.date_input("From", value=s.clock.today().replace(day=1))
        end = c2.date_input("To", value=s.clock.today())
        run = st.form_submit_button("Run report", type="primary")
    if run:
        fn = s.reports.stock_movement if kind == "Stock movement" else s.reports.demand
        st.session_state["report"] = attempt(fn, u, start, end)
    report = st.session_state.get("report")
    if not report:
        return
    st.subheader(f"{report['title']}: {report['start']} to {report['end']}")
    if report.get("note"):
        st.info(report["note"])
    if "summary" in report:
        cols = st.columns(3)
        cols[0].metric("Households served", report["summary"]["households_served"])
        cols[1].metric("Hampers distributed", report["summary"]["hampers_distributed"])
        cols[2].metric("Unmet requests", report["summary"]["unmet_requests"])
    if report["rows"]:
        st.dataframe(report["rows"], hide_index=True)
    st.download_button("Export to CSV", s.reports.to_csv(report),
                       file_name=f"{report['title'].lower().replace(' ', '_')}_{report['start']}_{report['end']}.csv",
                       mime="text/csv")
