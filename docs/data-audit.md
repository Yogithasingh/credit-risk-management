# Dataset audit and modeling choices

## Supplied file

- **File:** `accepted_2007_to_2018Q4.csv`
- **Shape:** 1,022 rows × 151 columns
- **Issue date:** all rows are `Dec-15`; there is no temporal range in this sample.
- **Duplicates:** 0 duplicate IDs and 0 exact duplicate rows.
- **Target column:** `loan_status`.
- **Resolved classes:** `Fully Paid` 746; `Charged Off` 145.
- **Unresolved classes:** `Current` 124; `Late (31-120 days)` 6; `In Grace Period` 1.
- **Resolved outcomes in the full file:** 891 rows (`Fully Paid` or `Charged Off`).
- **Joint applications:** 8 rows; 7 are resolved and 1 is current. They are excluded because the demo form models a single applicant and co-applicant inputs are sparse.
- **Training population:** 884 completed individual loans (740 `Fully Paid`, 144 `Charged Off`). The 130 unresolved individual loans are excluded.
- **Default share among the training population:** 144 / 884 = **16.29%**. This is a historical sample statistic, not the default rate of applications created in this app.

The file includes many partially observed, constant, irrelevant, and post-outcome fields. It also has extensive missingness in fields irrelevant to the selected model: e.g. `member_id`, `desc`, secondary-applicant fields, and most hardship fields are entirely empty in this extract. All selected inputs are present except `emp_length`, which is missing on 42 rows; preprocessing imputation remains in place for reproducibility and future data.

## Target and leakage review

The supervised target is a binary *historical individual-loan outcome*:

- `Charged Off` → 1 (default)
- `Fully Paid` → 0 (no recorded charge-off)
- Current/late/grace statuses → excluded (final outcome not known)
- Joint applications → excluded (the application form has no co-applicant inputs)

The source has records from after loan origination as well as fields known when the loan was priced. Those post-origination fields are not used: payment totals/principal/interest, recovery amounts and dates, last/next payment dates, last FICO, hardship, settlement, debt-settlement outcomes, and remaining principal. Identifier and free-text fields (`id`, member ID, URL, title/description, employer title), and location (`zip_code`, `addr_state`) are also excluded.

Origination pricing fields (`grade`, `sub_grade`, `int_rate`, installment, and funded amount) are omitted because they are lender-side decisions or derived values unavailable as reliable applicant inputs before assessment. This avoids training on a rating/pricing proxy the demo form cannot independently collect. The included FICO score is a midpoint derived from the supplied FICO low/high range.

The source does not supply a complete demographic set for fairness evaluation. The selected features are financial/profile variables, yet credit score, home ownership, and other financial records may still encode unequal historical treatment. Location is excluded, but that alone does not establish fairness. Any real lending use requires legal review, validated applicant data, fairness testing on an appropriate dataset, monitoring, and human oversight.

## Features used

The model uses 12 numeric inputs and three categoricals:

`loan_amount`, `term_months`, `annual_income`, `dti`, `prior_delinquencies`, `fico_score`, `recent_credit_inquiries`, `open_accounts`, `public_records`, `revolving_balance`, `revolving_utilization`, `total_accounts`, `home_ownership`, `employment_length`, and `purpose`.

The numeric monetary inputs `loan_amount`, `annual_income`, and `revolving_balance` are trained in USD units from LendingClub's `loan_amnt`, `annual_inc`, and `revol_bal` columns. Applicants may enter these amounts in USD, INR, GBP, or EUR. The backend uses a dated daily Frankfurter reference rate to convert the three values to USD before model scoring, saves the original amounts and currency, and records the source, rate date, and normalized USD values with the prediction. USD training ranges and warnings are therefore always expressed in USD. This denomination conversion does not support or validate credit applicants in non-US markets; the application market remains US and FICO/home-ownership/credit-system assumptions remain US-specific.

All 15 map to actual columns or transparent transformations in the supplied data. Employment length has 42 missing source rows and is mode-imputed during training; the input is optional in the application form. See the feature map in the README and the model metadata JSON generated on startup.

## Validation and limitations

The sample has one issue month, so a chronological holdout would be impossible. A reproducible stratified random split is used: 64% for candidate fitting, 16% for validation, and 20% held out for the final selected model. Candidate models are selected using validation PR-AUC, with default recall then ROC-AUC as tie-breakers. A small three-fold randomized search tunes the selected family on the training partition. Final metrics are calculated only on the held-out test rows.

This is a single small cohort and validation/test subsets are also small. A random split from the same month can overstate future performance. Metrics, thresholds, and feature rankings are demonstrative and not a credit policy. Thresholds are configurable display bands and do not make decisions.

## Outliers and preprocessing

No records are dropped for extreme but valid numeric values, and values are not winsorized. Numeric missing values are median-imputed and scaled; categoricals are mode-imputed and one-hot encoded with unseen values ignored. There is no synthetic oversampling. Class imbalance is handled with class weights where supported or a tuned XGBoost positive-class weight. Numeric form validation uses broad guardrails; when an input is outside the range observed in the model's training split, the prediction response flags it.

## Explanation limits

The UI's case-level factors are one-feature-at-a-time probability sensitivity relative to training-profile median/mode values. They do not sum to the prediction, can be distorted by correlated variables, and are not causal. Global permutation importance is computed on the small held-out test set, so its ordering can vary with a different split. These measures should not be presented as reasons that a person will default.
