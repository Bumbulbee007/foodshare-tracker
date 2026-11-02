# FoodShare Tracker

A human-centred community food bank management system (FIT1056 Deliverable 2, Monash University).

FoodShare Tracker helps a food bank record donations, keep stock in date, pack hampers that suit each
household's dietary, cultural and allergy needs, run a dignified pickup counter, and coordinate
volunteers. Clients are identified by a pickup code, never by name, on counter and packing screens,
and volunteers never see personal details.

It runs on one computer, offline. The browser window it opens is local (`localhost`); no data
leaves the computer.

## 1. Install (about 5 minutes)

You need **Python 3.11 or later** (python.org). Put the project in a normal folder such as
`C:\FIT1056\`, not inside OneDrive or Dropbox: cloud sync can briefly lock the data files while
the app saves them. Then, in a terminal opened in this folder (the one containing `app.py`):

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate
# macOS:    source .venv/bin/activate
pip install -r requirements.txt
```

## 2. Run

```bash
streamlit run app.py
```

Your browser opens at http://localhost:8501. On the very first run the app creates demo data in
`data/`. To start again with fresh demo data at any time:

```bash
python -m foodshare.seed --reset
```

### Demo log-ins

| Username | Password / PIN | Role | Persona |
| --- | --- | --- | --- |
| `admin` | `Admin#2026!` | Admin | Manager |
| `grace` | `Grace#2026!` | Staff | P1 Grace Tan, coordinator |
| `margaret` | PIN `1234` | Volunteer | P3 Margaret Wilson |
| `david` | PIN `5678` | Volunteer | – |

**Client kiosk:** on the log-in screen choose *Open the client kiosk*. Use Amina's pickup code
(shown on the *Hampers* page while logged in as staff) and kiosk PIN `2468`.

## 3. Run the tests

```bash
python -m pytest                    # all unit and functional tests
python -m pytest --cov=foodshare    # with statement coverage (needs pytest-cov)
```

`tests/unit/` tests each class and function (positive, negative and edge cases).
`tests/functional/` has one test per acceptance criterion (named `test_ftNN_acNN_...`),
two end-to-end persona scenarios, and the performance tests for NFR-15 and NFR-16.

## 4. Main features

| Feature | What you can do | Where |
| --- | --- | --- |
| F-01 Donations | Record donations (expired items refused), anonymous donors, confirmation slip; donor offers with priority needs | Donations |
| F-02 Stock | Stock by batch, nearest use-by date, expiring-soon list, low-stock alerts, flag damaged/expired/missing batches | Today, Stock |
| F-03 Households | Profiles with a consent question and plain-language reason for each sensitive category; restricted notes; show / correct / delete information on request | Households |
| F-04 Hampers and pickups | Pickup codes, message text with only time and code, rule-based hampers (allergen-safe, diet-matched, first-expiry-first-out), audited adjustments, safe substitutes, unmet needs, counter collection, returns | Households, Hampers, Pickup counter |
| F-05 Volunteer tasks | Create and assign tasks; volunteers see their own and open tasks, pack with tick boxes, choose safe substitutes, report problems | Volunteer tasks, My tasks |
| F-06 Access and privacy | Log-in with lock-out, 10-minute idle log-out, role permissions, audit log of every view/change/denied attempt, backups | everywhere; Admin pages |
| F-07 Reports | De-identified stock movement and demand reports, CSV export | Reports |
| F-08 Client kiosk | Change pickup time or contact method with code + PIN; one question per screen; resets after 60 seconds | Log-in screen |

## 5. Project structure (three layers, SRS Section 8)

```
app.py                  Presentation: log-in, menu by role, idle time-out
ui/                     Presentation: one module per user group (staff, volunteer, admin, kiosk)
foodshare/
  system.py             Wires the layers together (the only object screens talk to)
  security.py           Access control (role permission matrix) + audit log  <- privacy enforcement point
  services/             Application layer: one service per feature
    stock.py            F-01, F-02
    clients.py          F-03
    hampers.py          F-04 (allocation rules, FEFO, eligibility, pickups)
    tasks.py            F-05
    auth.py             F-06 log-in, lock-out, accounts
    reports.py          F-07
    kiosk.py            F-08
  models.py             Entity classes (see the class diagram)
  repository.py         Data layer: JSON files, atomic saves, append-only audit, daily backups
  text/en.json          All interface wording (plain English; ready for translation)
  text/banned_terms.json Words the system never displays (dignity, NFR-05)
  seed.py               Demo data
data/                   Created at first run (JSON files and backups/)
tests/                  PyTest unit and functional tests
```

Screens never read or write files; every action goes through a service, and every service starts
with an access-control check that is audited when refused (AD-02, NFR-18).

## 6. Known issues and limitations

- **One workstation at a time.** Data is held in memory and saved to JSON files (AD-04). Running the
  app on two computers against the same folder can overwrite changes. Multi-site use (F-11) would need
  a database server.
- **Messages are not sent automatically.** The app prepares pickup message text; staff send it from
  their own phone or email (AS-04, F-09 out of scope).
- **Only English text so far.** All wording is in `foodshare/text/en.json`; adding, for example,
  `"Arabic": "..."` under `pickup_message` makes Arabic messages work without code changes (F-12).
- **Scarce stock is allocated in booking order.** When stock runs short, households booked earlier are
  served first and the shortfall is recorded as an unmet need. Equal sharing of scarce stock across a
  whole day's bookings (Table 15 "Insufficient stock") is planned future work.
- **Idle time-outs need the browser tab open.** The 10-minute staff log-out and 60-second kiosk reset
  are checked every 5 seconds while the page is open; if the tab is closed the session simply ends.
- **The kiosk shares the staff computer's browser.** In a real deployment the kiosk would run in its own
  browser window or device; staff leave kiosk mode from the sidebar.
- **Demo passwords** are printed above and in `foodshare/seed.py`. Change them before real use.

## 7. Requirements

`requirements.txt` lists Streamlit (the interface) and PyTest with pytest-cov (testing).
Everything else is the Python standard library.
