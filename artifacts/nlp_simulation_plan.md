# Implementation Plan: End-to-End Live NLP & Simulation Architecture

This document outlines the step-by-step technical plan to build the "Simulate Vendor Event" feature, upgrade security, and connect the live Natural Language Processing (VADER) engine directly to the NoSQL database.

## Phase 1: Dependency & Environment Preparation
1. **Update `requirements.txt`**
   - Add `vaderSentiment==3.3.2` to enable live text parsing.
   - Add `nltk` if required by VADER tokenization.
2. **Security & Redirect Safety**
   - Verify `backend/routes/auth.py` strictly uses Flask signed `session` cookies.
   - Ensure redirects use `redirect(url_for(...))` instead of passing authentication tokens or passwords in URL query parameters (which prevents token leakage in browser history or server logs).
   - Ensure the `@login_required` decorator is strictly applied to the new simulation routes so unauthorized users cannot spam the database.

## Phase 2: Backend Logic (`backend/routes/simulator.py`)
1. **Create Simulator Blueprint**
   - Create a new Flask Blueprint `simulator_bp` to keep the architecture modular and prevent cluttering the main `app.py`.
2. **Live NLP Route (`GET /simulate`, `POST /simulate`)**
   - **GET:** Fetch the list of all active sellers from Firestore to populate the UI dropdown.
   - **POST:** 
     - Extract `seller_id`, `delay_days`, and `review_text` from the secure form payload.
     - **NLP Engine:** Pass `review_text` into `SentimentIntensityAnalyzer().polarity_scores()`.
     - **Weight Conversion:** Map the VADER `compound` score (which ranges from `-1` to `1`) into the `VaderSeverityWeight` system expected by the XGBoost pipeline (e.g., negative text creates a high positive severity penalty).
3. **Firestore Injection**
   - Generate a mock `poId` (Purchase Order ID).
   - Write a new document to the `deliveries` collection containing the `delayDays`.
   - Write a new document to the `quality_inspections` collection containing the `review_text` and calculated `VaderSeverityWeight`.

## Phase 3: Frontend UI (`simulate.html`)
1. **Design the Template**
   - Create `flask_webapp/templates/simulate.html`.
   - Re-use the sleek React/Vite/Tailwind-style glassmorphism CSS from the `add_seller.html` template.
   - Build a form with:
     - Dropdown for Seller selection.
     - Number input for Delay (Days).
     - Textarea for the Inspector's NLP Review.
     - Submit button.
2. **Dashboard Integration**
   - Add a "Simulate Event" button to the top navigation bar of the main dashboard (`index.html`), next to the "Add Seller" button, so it directs the user to a standalone page rather than cluttering the dashboard view.

## Phase 4: Compilation & Local Testing
1. **Syntax Validation**
   - Run `python -m py_compile` on the new `simulator.py` to guarantee zero syntax errors.
2. **Template Testing**
   - Instantiate a Flask test client locally to verify the `/simulate` route renders without Jinja compilation errors.
3. **Security Testing**
   - Verify that accessing `/simulate` while logged out correctly triggers a safe 302 redirect back to the `/login` page without leaking data.
