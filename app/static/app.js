const state = { user: null, page: 'dashboard', applicationPage: 1, applicationPages: 1, auditPage: 1, busy: false };
const byId = (id) => document.getElementById(id);

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character]);
}
function money(value) {
  if (value === null || value === undefined || value === '') return '—';
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(Number(value));
}
function number(value, digits = 0) {
  if (value === null || value === undefined || value === '') return '—';
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: digits }).format(Number(value));
}
function percent(value, digits = 1) {
  if (value === null || value === undefined || value === '') return '—';
  return `${(Number(value) * 100).toFixed(digits)}%`;
}
function dateText(value, includeTime = false) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return escapeHtml(value);
  return new Intl.DateTimeFormat(undefined, includeTime ? { dateStyle: 'medium', timeStyle: 'short' } : { dateStyle: 'medium' }).format(date);
}
function titleWords(value) {
  return String(value || '').toLowerCase().split(/[_\s]+/).map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join(' ');
}
function roleLabel(role) {
  return ({ APPLICANT: 'Applicant', ANALYST: 'Credit analyst', ADMIN: 'Administrator' })[role] || role;
}
function statusLabel(status) {
  return ({ AI_ASSESSED: 'AI assessed', UNDER_REVIEW: 'Under review', APPROVED: 'Approved', REJECTED: 'Rejected', NEEDS_MORE_INFORMATION: 'Information requested' })[status] || titleWords(status);
}
function riskClass(risk) {
  return risk ? `risk-${String(risk).toLowerCase()}` : 'risk-na';
}
function statusClass(status) {
  if (status === 'APPROVED') return 'status-approved';
  if (status === 'REJECTED') return 'status-rejected';
  if (status === 'NEEDS_MORE_INFORMATION') return 'status-needs';
  return 'status-pending';
}
function riskBadge(risk) {
  return `<span class="risk-badge ${riskClass(risk)}">${escapeHtml(risk || 'NOT ASSESSED')}</span>`;
}
function statusBadge(status) {
  return `<span class="status-badge ${statusClass(status)}">${escapeHtml(statusLabel(status))}</span>`;
}
function showNotice(message, kind = 'info', target = 'global-notice') {
  const element = byId(target);
  if (!element) return;
  element.className = `notice notice-${kind}${target === 'global-notice' ? ' global-notice' : ''}`;
  element.textContent = message;
  if (target === 'global-notice') window.scrollTo({ top: 0, behavior: 'smooth' });
}
function clearNotice(target = 'global-notice') {
  const element = byId(target);
  if (element) element.className = target === 'global-notice' ? 'notice global-notice hidden' : 'notice hidden';
}
function messageFrom(data) {
  if (data?.detail && Array.isArray(data.detail.errors)) return data.detail.errors.map((error) => `${error.field ? `${error.field}: ` : ''}${error.message}`).join(' · ');
  if (typeof data?.detail === 'string') return data.detail;
  return 'Something went wrong. Please try again.';
}
async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body !== undefined && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  const response = await fetch(path, { ...options, headers, credentials: 'same-origin' });
  const contentType = response.headers.get('content-type') || '';
  const data = contentType.includes('application/json') ? await response.json() : await response.text();
  if (!response.ok) {
    if (response.status === 401 && state.user) {
      state.user = null;
      showAuth();
    }
    throw new Error(messageFrom(data));
  }
  return data;
}
function formObject(form) {
  const data = Object.fromEntries(new FormData(form).entries());
  if (data.term_months !== undefined) data.term_months = Number(data.term_months);
  if (data.employment_length === '') data.employment_length = null;
  form.querySelectorAll('input[type="number"]').forEach((input) => {
    if (input.name && data[input.name] !== '') data[input.name] = Number(data[input.name]);
  });
  return data;
}

function showAuth() {
  byId('auth-shell').classList.remove('hidden');
  byId('workspace').classList.add('hidden');
  byId('login-form').classList.remove('hidden');
  byId('register-form').classList.add('hidden');
  byId('auth-title').textContent = 'Welcome back';
  byId('auth-subtitle').textContent = 'Sign in to continue to your credit risk workspace.';
  byId('auth-switch').innerHTML = `New to CrediGuard? <button type="button" class="text-button" data-action="show-register">Create an applicant account</button>`;
  clearNotice('auth-message');
}
function showRegister() {
  byId('login-form').classList.add('hidden');
  byId('register-form').classList.remove('hidden');
  byId('auth-title').textContent = 'Create your account';
  byId('auth-subtitle').textContent = 'Applicant accounts are private and can only see their own applications.';
  byId('auth-switch').innerHTML = `Already registered? <button type="button" class="text-button" data-action="show-login">Sign in</button>`;
  clearNotice('auth-message');
}
function rolePages() {
  if (state.user.role === 'APPLICANT') return [
    ['dashboard', 'Overview', '◫'], ['applications', 'My applications', '▤'], ['new-application', 'New application', '+'],
  ];
  const pages = [['dashboard', 'Overview', '◫'], ['applications', 'Applications', '▤'], ['model', 'Model & governance', '⌁']];
  if (state.user.role === 'ADMIN') pages.push(['users', 'User access', '♙'], ['policy', 'Risk settings', '⚙'], ['audit', 'Audit history', '◷']);
  return pages;
}
function showWorkspace() {
  byId('auth-shell').classList.add('hidden');
  byId('workspace').classList.remove('hidden');
  byId('user-name').textContent = state.user.full_name;
  byId('user-role').textContent = roleLabel(state.user.role);
  byId('user-avatar').textContent = state.user.full_name.trim().split(/\s+/).slice(0, 2).map((part) => part[0]).join('').toUpperCase();
  byId('topbar-date').textContent = new Intl.DateTimeFormat(undefined, { weekday: 'short', month: 'short', day: 'numeric' }).format(new Date());
  refreshHealth();
  const pages = rolePages();
  byId('main-nav').innerHTML = pages.map(([key, label, icon]) => `<button class="nav-item ${state.page === key ? 'active' : ''}" type="button" data-action="nav" data-page="${key}"><span class="nav-icon">${icon}</span><span>${label}</span></button>`).join('');
  if (!pages.some(([key]) => key === state.page)) state.page = 'dashboard';
  renderPage();
}
async function refreshHealth() {
  try {
    const health = await api('/api/health');
    byId('system-status-label').textContent = health.status === 'ok' ? 'System operational' : 'System degraded';
    byId('system-status-dot').classList.toggle('degraded', health.status !== 'ok');
  } catch {
    byId('system-status-label').textContent = 'System status unavailable';
    byId('system-status-dot').classList.add('degraded');
  }
}
function setPageHeader() {
  const pages = Object.fromEntries(rolePages().map(([key, title]) => [key, title]));
  const title = pages[state.page] || (state.page === 'application-detail' ? 'Application review' : 'Credit risk workspace');
  byId('page-title').textContent = title;
  byId('page-kicker').textContent = state.user.role === 'APPLICANT' ? 'APPLICANT PORTAL' : state.user.role === 'ADMIN' ? 'RISK OPERATIONS' : 'CREDIT RISK PLATFORM';
  byId('main-nav').querySelectorAll('.nav-item').forEach((item) => item.classList.toggle('active', item.dataset.page === state.page));
}
function navigate(page, data = {}) {
  state.page = page;
  if (data.id) state.applicationId = data.id;
  if (page === 'applications') state.applicationPage = 1;
  if (page === 'audit') state.auditPage = 1;
  setPageHeader();
  byId('sidebar').classList.remove('open');
  renderPage();
}
async function renderPage() {
  if (!state.user) return;
  setPageHeader();
  clearNotice();
  byId('page').innerHTML = `<div class="loading"><span class="spinner"></span>Loading your workspace…</div>`;
  try {
    if (state.page === 'dashboard') await renderDashboard();
    else if (state.page === 'applications') await renderApplications();
    else if (state.page === 'new-application') renderApplicationForm();
    else if (state.page === 'application-detail') await renderApplicationDetail(state.applicationId);
    else if (state.page === 'model') await renderModel();
    else if (state.page === 'users') await renderUsers();
    else if (state.page === 'policy') await renderPolicy();
    else if (state.page === 'audit') await renderAudit();
    else state.page = 'dashboard';
  } catch (error) {
    if (state.user) {
      byId('page').innerHTML = `<div class="panel"><div class="empty-state"><strong>We couldn't load this view</strong>${escapeHtml(error.message)}</div></div>`;
      showNotice(error.message, 'error');
    }
  }
}

function metricCard(label, value, icon, foot, color = '') {
  return `<article class="metric-card ${color}"><div class="metric-head"><span>${escapeHtml(label)}</span><span class="metric-icon">${icon}</span></div><strong>${escapeHtml(value)}</strong><div class="metric-foot">${escapeHtml(foot)}</div></article>`;
}
function chartBars(distribution = {}) {
  const items = [['LOW', 'Low risk', ''], ['MEDIUM', 'Medium risk', 'medium'], ['HIGH', 'High risk', 'high']];
  const max = Math.max(1, ...items.map(([key]) => Number(distribution[key] || 0)));
  return `<div class="chart-bars">${items.map(([key, label, color]) => {
    const count = Number(distribution[key] || 0);
    return `<div class="bar-row"><span>${label}</span><div class="bar-track"><div class="bar-fill ${color}" style="width:${Math.max(count ? 4 : 0, count / max * 100)}%"></div></div><strong>${count}</strong></div>`;
  }).join('')}</div>`;
}
function monthChart(items = []) {
  const max = Math.max(1, ...items.map((item) => item.count));
  return `<div class="mini-bars">${items.length ? items.map((item) => `<div class="mini-bar-col"><div class="mini-bar" style="height:${Math.max(4, item.count / max * 84)}%" title="${item.count} application(s)"></div><small>${escapeHtml(String(item.month).slice(5))}</small></div>`).join('') : '<span class="empty-inline">Applications will appear here as they are submitted.</span>'}</div>`;
}
function applicationRows(items, staff = false) {
  if (!items.length) return `<tr><td colspan="7"><div class="empty-state"><strong>No applications yet</strong>${staff ? 'Submitted applications will appear in this queue.' : 'Start an application when you are ready.'}</div></td></tr>`;
  return items.map((item) => `<tr data-action="open-app" data-id="${escapeHtml(item.id)}">
    <td><span class="table-primary">${escapeHtml(item.applicant_name || 'Applicant')}</span><span class="table-secondary">${escapeHtml(item.id.slice(0, 8).toUpperCase())} · ${dateText(item.created_at)}</span></td>
    <td>${staff ? escapeHtml(item.applicant_email || '') : `${number(item.application_data.term_months)} months`}</td>
    <td>${money(item.application_data.loan_amount)}</td>
    <td>${item.prediction ? percent(item.prediction.probability_of_default) : '—'}</td>
    <td>${riskBadge(item.prediction?.risk_category)}</td>
    <td>${statusBadge(item.status)}</td>
    <td><button type="button" class="button button-outline button-small" data-action="open-app" data-id="${escapeHtml(item.id)}">Open</button></td>
  </tr>`).join('');
}
function applicationsTable(items, staff = false, total = items.length, page = 1, pages = 1) {
  return `<div class="panel"><div class="table-wrap"><table class="data-table"><thead><tr><th>Applicant / reference</th><th>${staff ? 'Email' : 'Term'}</th><th>Loan amount</th><th>Default estimate</th><th>Risk</th><th>Status</th><th></th></tr></thead><tbody>${applicationRows(items, staff)}</tbody></table></div><div class="table-pagination"><span>${total ? `Showing ${(page - 1) * 20 + 1}–${Math.min(page * 20, total)} of ${total}` : 'No records'}</span><div class="pagination-actions"><button type="button" class="button button-outline button-small" data-action="app-page" data-page-number="${Math.max(1, page - 1)}" ${page <= 1 ? 'disabled' : ''}>Previous</button><button type="button" class="button button-outline button-small" data-action="app-page" data-page-number="${Math.min(pages, page + 1)}" ${page >= pages ? 'disabled' : ''}>Next</button></div></div></div>`;
}

async function renderDashboard() {
  const summary = await api('/api/dashboard/summary');
  if (summary.scope === 'applicant') {
    const items = summary.applications || [];
    byId('page').innerHTML = `<div class="page-heading"><div><div class="eyebrow-sub">YOUR CREDIT JOURNEY</div><h2>Welcome, ${escapeHtml(state.user.full_name.split(' ')[0])}</h2><p>Track each application and view your machine-learning assessment.</p></div><div class="page-actions"><button class="button button-primary" data-action="nav" data-page="new-application">＋ Start an application</button></div></div>
      <div class="metrics-grid">${metricCard('Your applications', number(summary.total_applications), '▤', 'Applications submitted to the credit team')}${metricCard('In progress', number(summary.in_progress), '◷', 'Waiting for review or your response', 'metric-blue')}</div>
      <div class="page-heading"><div><h2>Recent applications</h2><p>Your risk estimate supports review; it does not decide the outcome.</p></div><button class="button button-outline" data-action="nav" data-page="applications">View all →</button></div>
      ${applicationsTable(items.slice(0, 5), false, items.slice(0, 5).length, 1, 1)}
      <div class="callout important" style="margin-top:15px">Your estimate is probabilistic and uses a small historical LendingClub sample. It is not a guarantee or a lending decision.</div>`;
    return;
  }
  const recent = await api('/api/applications?page=1&page_size=6');
  const dist = summary.risk_distribution || {};
  const rate = summary.historical_default_rate;
  byId('page').innerHTML = `<div class="page-heading"><div><div class="eyebrow-sub">PORTFOLIO SNAPSHOT</div><h2>Credit operations overview</h2><p>Application workflow and model assessments from this workspace.</p></div><div class="page-actions"><a class="button button-outline" href="/api/reports/applications.csv">↓ Export applications</a><a class="button button-outline" href="/api/reports/powerbi.csv">↗ BI-ready CSV</a><button class="button button-primary" data-action="nav" data-page="applications">Review queue →</button></div></div>
    <div class="metrics-grid">${metricCard('Total applications', number(summary.total_applications), '▤', 'All submitted applications')}${metricCard('Pending review', number(summary.pending_review), '◷', 'AI assessed, in review, or awaiting information', 'metric-blue')}${metricCard('Approved', number(summary.approved), '✓', 'Analyst decisions', '')}${metricCard('High risk', number(dist.HIGH || 0), '!', 'Manual review remains required', 'metric-red')}${metricCard('Medium risk', number(dist.MEDIUM || 0), '◉', 'Latest assessment on each application', 'metric-amber')}${metricCard('Low risk', number(dist.LOW || 0), '○', 'Latest assessment on each application', '')}${metricCard('Average loan amount', money(summary.average_loan_amount), '$', 'Submitted applications')}${metricCard('Average risk score', `${number(summary.average_risk_score, 1)} / 100`, '⌁', 'Probability of default × 100', 'metric-blue')}</div>
    <div class="content-grid"><section class="panel"><div class="panel-head"><div><div class="panel-title">Risk profile</div><div class="panel-subtitle">Latest estimate per application</div></div><span class="role-badge">${number(summary.total_applications)} total</span></div><div class="panel-body">${chartBars(dist)}</div></section><section class="panel"><div class="panel-head"><div><div class="panel-title">Application volume</div><div class="panel-subtitle">Submitted by month</div></div></div><div class="panel-body">${monthChart(summary.applications_by_month)}</div></section></div>
    <div class="panel"><div class="panel-head"><div><div class="panel-title">Latest applications</div><div class="panel-subtitle">Click a row to open the review and audit history</div></div><button class="button button-outline button-small" data-action="nav" data-page="applications">All applications →</button></div><div class="table-wrap"><table class="data-table"><thead><tr><th>Applicant / reference</th><th>Email</th><th>Loan amount</th><th>Default estimate</th><th>Risk</th><th>Status</th><th></th></tr></thead><tbody>${applicationRows(recent.items, true)}</tbody></table></div></div>
    <div class="callout warning" style="margin-top:15px"><strong>Historical data note.</strong> ${rate === null ? 'Training cohort default share is unavailable.' : `The matured training cohort default share was ${percent(rate)}.`} ${escapeHtml(summary.historical_default_rate_note || '')} It is separate from current application outcomes.</div>`;
}

async function renderApplications() {
  const staff = state.user.role !== 'APPLICANT';
  const params = new URLSearchParams({ page: String(state.applicationPage), page_size: '20' });
  if (staff) {
    const search = byId('application-search')?.value.trim();
    const status = byId('application-status-filter')?.value;
    const risk = byId('application-risk-filter')?.value;
    if (search) params.set('search', search);
    if (status) params.set('status', status);
    if (risk) params.set('risk', risk);
  }
  const result = await api(`/api/applications?${params}`);
  state.applicationPages = result.pages;
  const controls = staff ? `<form id="application-filter" class="table-toolbar"><input id="application-search" class="search-input" type="search" value="${escapeHtml(byId('application-search')?.value || '')}" placeholder="Applicant, email, or ID"><select id="application-status-filter" class="filter-select"><option value="">All statuses</option>${[['AI_ASSESSED','AI assessed'],['UNDER_REVIEW','Under review'],['NEEDS_MORE_INFORMATION','Information requested'],['APPROVED','Approved'],['REJECTED','Rejected']].map(([value,label])=>`<option value="${value}" ${byId('application-status-filter')?.value===value?'selected':''}>${label}</option>`).join('')}</select><select id="application-risk-filter" class="filter-select"><option value="">All risk</option>${['LOW','MEDIUM','HIGH'].map((value)=>`<option ${byId('application-risk-filter')?.value===value?'selected':''}>${value}</option>`).join('')}</select><button class="button button-outline button-small" type="submit">Filter</button></form>` : `<span class="role-badge">Private to your account</span>`;
  byId('page').innerHTML = `<div class="page-heading"><div><div class="eyebrow-sub">${staff ? 'CREDIT REVIEW QUEUE' : 'YOUR HISTORY'}</div><h2>${staff ? 'Applications' : 'My applications'}</h2><p>${staff ? 'Filter and review submitted loan applications.' : 'Every application and its latest assessment.'}</p></div><div class="page-actions">${staff ? '<a class="button button-outline" href="/api/reports/applications.csv">↓ Export CSV</a><a class="button button-outline" href="/api/reports/powerbi.csv">↗ BI-ready CSV</a>' : '<button class="button button-primary" data-action="nav" data-page="new-application">＋ New application</button>'}</div></div>
    <div class="panel"><div class="panel-head"><div><div class="panel-title">${result.total} application${result.total === 1 ? '' : 's'}</div><div class="panel-subtitle">Most recent first</div></div>${controls}</div><div class="table-wrap"><table class="data-table"><thead><tr><th>Applicant / reference</th><th>${staff?'Email':'Term'}</th><th>Loan amount</th><th>Default estimate</th><th>Risk</th><th>Status</th><th></th></tr></thead><tbody>${applicationRows(result.items, staff)}</tbody></table></div><div class="table-pagination"><span>${result.total ? `Page ${result.page} of ${result.pages} · ${result.total} results` : 'No records'}</span><div class="pagination-actions"><button type="button" class="button button-outline button-small" data-action="app-page" data-page-number="${Math.max(1, result.page - 1)}" ${result.page <= 1 ? 'disabled' : ''}>Previous</button><button type="button" class="button button-outline button-small" data-action="app-page" data-page-number="${Math.min(result.pages, result.page + 1)}" ${result.page >= result.pages ? 'disabled' : ''}>Next</button></div></div></div>`;
}

const purposeOptions = [
  ['debt_consolidation', 'Debt consolidation'], ['credit_card', 'Credit card'], ['home_improvement', 'Home improvement'], ['other', 'Other'],
  ['major_purchase', 'Major purchase'], ['car', 'Car'], ['small_business', 'Small business'], ['house', 'House'], ['moving', 'Moving'], ['vacation', 'Vacation'], ['medical', 'Medical'],
];
const applicationFields = [
  { name: 'loan_amount', label: 'Requested loan amount', type: 'number', min: 1, max: 1000000, step: 100, hint: 'USD. The historical sample ranges from $1,000 to $35,000.' },
  { name: 'term_months', label: 'Loan term', type: 'select', options: [[36, '36 months'], [60, '60 months']], hint: 'Only the two terms found in the supplied sample are supported.' },
  { name: 'annual_income', label: 'Annual income', type: 'number', min: 1, max: 100000000, step: 1000, hint: 'USD, before tax.' },
  { name: 'home_ownership', label: 'Home ownership', type: 'select', options: [['MORTGAGE', 'Mortgage'], ['RENT', 'Rent'], ['OWN', 'Own']], hint: 'Categories available in the dataset.' },
  { name: 'employment_length', label: 'Employment length', type: 'select', required: false, options: [['', 'Not provided'], ['< 1 year', 'Less than 1 year'], ['1 year', '1 year'], ['2 years', '2 years'], ['3 years', '3 years'], ['4 years', '4 years'], ['5 years', '5 years'], ['6 years', '6 years'], ['7 years', '7 years'], ['8 years', '8 years'], ['9 years', '9 years'], ['10+ years', '10+ years']], hint: 'Observed categories from the supplied data; optional when unavailable.' },
  { name: 'purpose', label: 'Loan purpose', type: 'select', options: purposeOptions, hint: 'Choose from purposes represented in the dataset.' },
  { name: 'dti', label: 'Debt-to-income ratio', type: 'number', min: 0, max: 200, step: 0.01, suffix: '%', hint: 'Existing monthly debt relative to monthly income.' },
  { name: 'prior_delinquencies', label: 'Prior delinquencies', type: 'number', min: 0, max: 100, step: 1, hint: 'Delinquencies recorded in the prior two years.' },
  { name: 'fico_score', label: 'FICO score', type: 'number', min: 300, max: 850, step: 1, hint: 'The model maps the dataset FICO band to its midpoint. The sample observed 662–847.5.' },
  { name: 'recent_credit_inquiries', label: 'Recent credit inquiries', type: 'number', min: 0, max: 100, step: 1, hint: 'Credit inquiries in the past six months.' },
  { name: 'open_accounts', label: 'Open credit accounts', type: 'number', min: 0, max: 500, step: 1 },
  { name: 'public_records', label: 'Public records', type: 'number', min: 0, max: 100, step: 1 },
  { name: 'revolving_balance', label: 'Revolving balance', type: 'number', min: 0, max: 100000000, step: 100, hint: 'USD.' },
  { name: 'revolving_utilization', label: 'Revolving utilization', type: 'number', min: 0, max: 300, step: 0.1, suffix: '%', hint: 'The supplied sample ranges from 0% to 102.4%.' },
  { name: 'total_accounts', label: 'Total credit accounts', type: 'number', min: 0, max: 1000, step: 1 },
];
function fieldHtml(field, value = '') {
  const label = `<label>${escapeHtml(field.label)}${field.suffix ? ` (${escapeHtml(field.suffix)})` : ''}${field.hint ? `<span class="field-hint">${escapeHtml(field.hint)}</span>` : ''}`;
  if (field.type === 'select') {
    const options = field.options.map(([optionValue, optionLabel]) => `<option value="${escapeHtml(optionValue)}" ${String(value) === String(optionValue) ? 'selected' : ''}>${escapeHtml(optionLabel)}</option>`).join('');
    return `<div class="field">${label}<select name="${field.name}" ${field.required === false ? '' : 'required'}>${field.required === false ? '' : `<option value="" disabled ${value === '' ? 'selected' : ''}>Select…</option>`}${options}</select></label></div>`;
  }
  return `<div class="field">${label}<input name="${field.name}" type="number" min="${field.min}" max="${field.max}" step="${field.step}" value="${escapeHtml(value)}" required></label></div>`;
}
function renderApplicationForm(existing = null) {
  const values = existing || {};
  const edit = Boolean(existing);
  byId('page').innerHTML = `<div class="page-heading"><div><div class="eyebrow-sub">${edit ? 'APPLICANT RESPONSE' : 'APPLICANT WORKFLOW'}</div><h2>${edit ? 'Provide the requested information' : 'New loan application'}</h2><p>${edit ? 'Update the financial inputs and send the application back for review.' : 'Enter the application and credit-profile fields supported by the historical data.'}</p></div>${!edit ? '<span class="demo-chip">DEMO VALUES AVAILABLE</span>' : ''}</div>
    <div class="callout warning" style="margin-bottom:15px"><strong>Data scope.</strong> The model was trained on 891 completed loans from one December 2015 LendingClub cohort. Credit-profile fields here are demonstration inputs; a real institution would validate them against authorized records.</div>
    ${!edit ? '<div class="page-actions" style="margin-bottom:13px"><button class="button button-outline button-small" type="button" data-action="fill-demo">Fill example values</button><span class="field-hint">Example values only; they are not a labeled risk outcome.</span></div>' : ''}
    <form id="application-form" data-edit-id="${escapeHtml(state.applicationId || '')}">
      <section class="form-card"><div class="form-section-head"><strong>Loan request</strong><span>Requested terms</span></div><div class="form-section-body"><div class="field-grid">${applicationFields.slice(0, 2).map((field) => fieldHtml(field, values[field.name])).join('')}${fieldHtml(applicationFields[5], values.purpose)}</div></div></section>
      <section class="form-card"><div class="form-section-head"><strong>Income &amp; credit profile</strong><span>Fields represented in the dataset</span></div><div class="form-section-body"><div class="field-grid">${applicationFields.slice(2, 5).map((field) => fieldHtml(field, values[field.name])).join('')}${applicationFields.slice(6).map((field) => fieldHtml(field, values[field.name])).join('')}</div></div></section>
      <div class="form-foot"><small>Assessment is a probability estimate. It does not automatically approve or reject a loan. Fields not present in the training data are not requested.</small><button class="button button-primary" type="submit">${edit ? 'Resubmit for review' : 'Submit application'} <span aria-hidden="true">→</span></button></div>
    </form>`;
}

function factorRows(factors = []) {
  if (!factors.length) return '<div class="empty-inline">No sensitivity information is available.</div>';
  const max = Math.max(0.01, ...factors.map((factor) => Math.abs(Number(factor.probability_change))));
  return `<div class="factor-list">${factors.map((factor) => {
    const change = Number(factor.probability_change || 0);
    const width = Math.max(3, Math.abs(change) / max * 100);
    const arrow = change > 0.0005 ? '↑' : change < -0.0005 ? '↓' : '·';
    return `<div class="factor-row"><span>${escapeHtml(factor.label)}</span><div class="factor-track" title="One-feature-at-a-time sensitivity"><span class="${change > 0 ? 'risk-up' : ''}" style="width:${width}%"></span></div><span class="factor-value">${arrow} ${change > 0 ? '+' : ''}${(change * 100).toFixed(1)} pp</span></div>`;
  }).join('')}</div>`;
}
function detailFields(data) {
  const entries = [
    ['Requested amount', money(data.loan_amount)], ['Term', `${number(data.term_months)} months`], ['Annual income', money(data.annual_income)],
    ['Home ownership', titleWords(data.home_ownership)], ['Employment length', data.employment_length ? titleWords(data.employment_length) : 'Not provided'], ['Loan purpose', titleWords(data.purpose)], ['Debt-to-income', `${number(data.dti, 2)}%`],
    ['FICO score', number(data.fico_score)], ['Prior delinquencies', number(data.prior_delinquencies)], ['Recent inquiries', number(data.recent_credit_inquiries)],
    ['Open accounts', number(data.open_accounts)], ['Public records', number(data.public_records)], ['Revolving balance', money(data.revolving_balance)],
    ['Revolving utilization', `${number(data.revolving_utilization, 1)}%`], ['Total accounts', number(data.total_accounts)],
  ];
  return `<div class="detail-list">${entries.map(([label, value]) => `<div class="detail-field"><small>${escapeHtml(label)}</small><strong>${escapeHtml(value)}</strong></div>`).join('')}</div>`;
}
function timelineHtml(events = []) {
  if (!events.length) return '<div class="empty-inline">No activity recorded yet.</div>';
  const labels = {
    APPLICATION_SUBMITTED: 'Application submitted', PREDICTION_GENERATED: 'Risk assessment generated', APPLICATION_REASSESSED: 'New risk assessment generated', ANALYST_REVIEW_STARTED: 'Analyst review started',
    ANALYST_DECISION_RECORDED: 'Analyst decision recorded', ANALYST_APPLICATION_VIEWED: 'Application reviewed by credit team', APPLICANT_INFORMATION_RESUBMITTED: 'Requested information resubmitted',
  };
  return `<div class="timeline">${events.map((event) => `<div class="timeline-item"><strong>${escapeHtml(labels[event.action] || titleWords(event.action))}${event.actor ? ` · ${escapeHtml(event.actor)}` : ''}</strong><small>${dateText(event.created_at, true)}</small>${event.details?.notes ? `<small>${escapeHtml(event.details.notes)}</small>` : ''}</div>`).join('')}</div>`;
}
async function renderApplicationDetail(id) {
  const item = await api(`/api/applications/${encodeURIComponent(id)}`);
  const staff = state.user.role !== 'APPLICANT';
  const prediction = item.prediction;
  const risk = prediction?.risk_category || '';
  const warningBlock = prediction?.range_warnings?.length ? `<div class="callout warning" style="margin-top:12px"><strong>Outside training range.</strong> ${prediction.range_warnings.map((warning) => `${escapeHtml(warning.label)} ${escapeHtml(number(warning.submitted_value, 1))} is outside ${escapeHtml(number(warning.observed_minimum, 1))}–${escapeHtml(number(warning.observed_maximum, 1))}. Treat this estimate cautiously.`).join('<br>')}</div>` : '';
  const applicantResponse = !staff && item.status === 'NEEDS_MORE_INFORMATION';
  const canReassess = staff && ['AI_ASSESSED', 'UNDER_REVIEW', 'NEEDS_MORE_INFORMATION'].includes(item.status);
  const reassessButton = canReassess ? `<button type="button" class="button button-outline" data-action="reassess" data-id="${escapeHtml(item.id)}">New assessment</button>` : '';
  const actions = staff ? `${reassessButton}${item.status === 'AI_ASSESSED'
    ? `<button type="button" class="button button-primary" data-action="start-review" data-id="${escapeHtml(item.id)}">Start analyst review</button>`
    : item.status === 'UNDER_REVIEW'
      ? `<button type="button" class="button button-outline" data-action="decision" data-decision="APPROVED" data-id="${escapeHtml(item.id)}">Approve</button><button type="button" class="button button-danger" data-action="decision" data-decision="REJECTED" data-id="${escapeHtml(item.id)}">Reject</button><button type="button" class="button button-outline" data-action="decision" data-decision="NEEDS_MORE_INFORMATION" data-id="${escapeHtml(item.id)}">Request information</button>`
      : ''}` : applicantResponse ? `<button type="button" class="button button-primary" data-action="edit-application" data-id="${escapeHtml(item.id)}">Provide information</button>` : '';
  const notePanel = staff && item.analyst_notes ? `<section class="panel"><div class="panel-head"><div class="panel-title">Analyst notes</div></div><div class="panel-body"><div class="callout">${escapeHtml(item.analyst_notes)}</div></div></section>` : !staff && applicantResponse && item.analyst_notes ? `<section class="panel"><div class="panel-head"><div class="panel-title">Information requested</div></div><div class="panel-body"><div class="callout warning">${escapeHtml(item.analyst_notes)}</div></div></section>` : '';
  const factorsBlock = prediction ? `<section class="panel"><div class="panel-head"><div><div class="panel-title">Prediction sensitivities</div><div class="panel-subtitle">How individual inputs changed the estimate relative to their training-profile reference</div></div></div><div class="panel-body">${factorRows(prediction.factors)}<div class="field-hint" style="margin-top:13px">This one-feature-at-a-time comparison is non-additive and not causal. Correlated inputs can affect each other; it is not SHAP.</div></div></section>` : '';
  const ownerCard = `<section class="panel"><div class="panel-head"><div><div class="panel-title">Applicant &amp; loan profile</div><div class="panel-subtitle">${staff ? `${escapeHtml(item.applicant_name)} · ${escapeHtml(item.applicant_email || '')}` : 'Your submitted details'}</div></div></div><div class="panel-body">${detailFields(item.application_data)}</div></section>`;
  const bands = prediction?.thresholds ? `LOW ≤ ${number(prediction.thresholds.low_risk_maximum * 100,0)}% · MEDIUM ≤ ${number(prediction.thresholds.medium_risk_maximum * 100,0)}%` : 'Risk thresholds unavailable';
  const assessment = prediction ? `<section class="assessment-card ${escapeHtml(risk.toLowerCase())}"><div><div class="assessment-label">RISK CATEGORY</div><div class="assessment-category ${escapeHtml(risk.toLowerCase())}">${escapeHtml(risk)} RISK</div><div class="assessment-caption">${escapeHtml(bands)}</div></div><div class="risk-score-block"><div class="assessment-label">PROBABILITY OF DEFAULT</div><div class="assessment-value">${percent(prediction.probability_of_default)}</div><div class="assessment-meter"><span style="width:${Math.max(0,Math.min(100,prediction.risk_score))}%"></span></div></div><div><div class="assessment-label">RISK SCORE</div><div class="assessment-value">${number(prediction.risk_score,1)} <span class="assessment-caption">/ 100</span></div><div class="assessment-caption">${escapeHtml(prediction.model_version)} · ${escapeHtml(item.prediction ? dateText(prediction.created_at) : '')}</div></div></section>` : '<div class="callout warning">No assessment is available for this application yet.</div>';
  const history = (item.prediction_history || []).length > 1 ? `<section class="panel"><div class="panel-head"><div><div class="panel-title">Prediction history</div><div class="panel-subtitle">Each assessment is retained with its model version and risk bands</div></div></div><div class="table-wrap"><table class="data-table"><thead><tr><th>Date</th><th>Model version</th><th>Probability</th><th>Risk bands</th><th>Risk</th></tr></thead><tbody>${item.prediction_history.map((entry)=>`<tr><td>${dateText(entry.created_at,true)}</td><td>${escapeHtml(entry.model_version)}</td><td>${percent(entry.probability_of_default)}</td><td>${entry.thresholds ? `≤${number(entry.thresholds.low_risk_maximum*100,0)}% / ≤${number(entry.thresholds.medium_risk_maximum*100,0)}%` : 'Unknown'}</td><td>${riskBadge(entry.risk_category)}</td></tr>`).join('')}</tbody></table></div></section>` : '';
  const timeline = item.timeline ? `<section class="panel"><div class="panel-head"><div><div class="panel-title">Application history</div><div class="panel-subtitle">Recorded actions</div></div></div><div class="panel-body">${timelineHtml(item.timeline)}</div></section>` : '';
  byId('page').innerHTML = `<div class="detail-header"><div><button class="text-button" data-action="nav" data-page="applications">← Back to applications</button><div class="detail-id" style="margin-top:9px">Application ${escapeHtml(item.id.slice(0, 8).toUpperCase())}</div><div class="detail-meta">Submitted ${dateText(item.created_at, true)} · ${statusBadge(item.status)}${item.decision ? ` · Decision: ${escapeHtml(statusLabel(item.decision))}` : ''}</div></div><div class="detail-actions"><a class="button button-outline" href="/api/reports/applications/${encodeURIComponent(item.id)}.pdf">Download PDF</a>${actions}</div></div>
    ${assessment}${warningBlock}
    <div class="detail-grid" style="margin-top:15px"><div style="display:grid;gap:15px">${ownerCard}${factorsBlock}${notePanel}</div><div style="display:grid;align-content:start;gap:15px"><section class="panel"><div class="panel-head"><div><div class="panel-title">Human review</div><div class="panel-subtitle">Final outcome remains with authorized staff</div></div></div><div class="panel-body">${item.decision ? `<div class="detail-field"><small>Recorded decision</small><strong>${escapeHtml(statusLabel(item.decision))}</strong></div><div class="detail-field" style="margin-top:13px"><small>Decision date</small><strong>${dateText(item.decision_at, true)}</strong></div>` : `<div class="callout">${staff ? 'Review the application details, consider the risk estimate, and record your own decision.' : 'The risk estimate supports a human review. It does not automatically approve or reject an application.'}</div>`}</div></section>${history}${timeline}</div></div>
    <div class="callout important" style="margin-top:15px">${staff ? 'Model explanations describe association with the estimated probability, not cause. A high-risk estimate must not be used as an automatic rejection.' : 'This assessment is generated by a machine-learning model and supports—not replaces—human credit decisions.'}</div>`;
}

async function renderModel() {
  const result = await api('/api/models/active');
  const metadata = result.metadata;
  const metrics = metadata.test_metrics;
  const comparisons = metadata.validation_comparison || [];
  const featureImportance = metadata.feature_importance || [];
  const maxImportance = Math.max(0.001, ...featureImportance.map((item) => Math.max(0, item.importance_mean)));
  const comparisonRows = comparisons.map((model) => `<tr><td>${escapeHtml(model.model)}</td><td>${number(model.accuracy * 100,1)}%</td><td>${number(model.precision * 100,1)}%</td><td>${number(model.recall * 100,1)}%</td><td>${number(model.f1 * 100,1)}%</td><td>${number(model.roc_auc * 100,1)}%</td><td>${number(model.pr_auc * 100,1)}%</td></tr>`).join('');
  const importanceRows = featureImportance.slice(0, 8).map((item) => `<div class="bar-row"><span>${escapeHtml(item.label)}</span><div class="bar-track"><div class="bar-fill blue" style="width:${Math.max(1, Math.max(0,item.importance_mean) / maxImportance * 100)}%"></div></div><strong>${number(item.importance_mean,3)}</strong></div>`).join('');
  const versionRows = result.versions.map((version) => `<tr><td>${escapeHtml(version.version)}</td><td>${escapeHtml(version.algorithm)}</td><td>${dateText(version.trained_at)}</td><td>${version.active ? '<span class="status-badge status-approved">Active</span>' : '<span class="risk-badge risk-na">Archived</span>'}</td></tr>`).join('');
  const outcomeCounts = metadata.dataset.target_counts || {};
  byId('model-footer').textContent = `${metadata.algorithm} · ${metadata.model_version}`;
  byId('page').innerHTML = `<div class="page-heading"><div><div class="eyebrow-sub">MODEL CARD &amp; MONITORING</div><h2>Model &amp; governance</h2><p>Metrics from the actual supplied data and the selected model artifact.</p></div>${state.user.role === 'ADMIN' ? '<button class="button button-primary" data-action="retrain">↻ Retrain from bundled dataset</button>' : ''}</div>
    <div class="callout warning" style="margin-bottom:15px"><strong>Scope limitation.</strong> ${metadata.dataset.rows_after_duplicate_id_removal} source rows, ${metadata.dataset.mature_rows_used} completed individual outcomes, ${metadata.dataset.unresolved_rows_excluded} unresolved individual loans, and ${metadata.dataset.joint_or_other_rows_excluded} joint/other applications excluded. The source has ${escapeHtml((metadata.dataset.issue_months || []).join(', ') || 'one recorded issue month')} only. Holdout metrics are illustrative for this cohort and do not establish performance on new years, lenders, or applicants; model probabilities are not calibrated for real lending.</div>
    <div class="metrics-grid">${metricCard('Active algorithm', metadata.algorithm, '⌁', metadata.model_version)}${metricCard('Mature training records', number(metadata.dataset.mature_rows_used), '▦', `${number(outcomeCounts['Charged Off'])} charged off · ${number(outcomeCounts['Fully Paid'])} fully paid`, 'metric-blue')}${metricCard('Held-out ROC-AUC', number(metrics.roc_auc * 100,1) + '%', '↗', 'Stratified test split · random state 42')}${metricCard('Held-out PR-AUC', number(metrics.pr_auc * 100,1) + '%', '◉', 'Average precision for default class', 'metric-amber')}${metricCard('Precision', number(metrics.precision * 100,1) + '%', '✓', 'At 0.5 classification threshold')}${metricCard('Default recall', number(metrics.recall * 100,1) + '%', '!', 'At 0.5 classification threshold', 'metric-red')}${metricCard('Predictions recorded', number(result.prediction_count), '⌁', 'Immutable prediction history')}${metricCard('Features used', number(metadata.features.length), '▤', `Trained ${dateText(metadata.trained_at)}`, 'metric-blue')}</div>
    <div class="content-grid"><section class="panel"><div class="panel-head"><div><div class="panel-title">Validation comparison</div><div class="panel-subtitle">Candidate models compared before tuning the selected candidate</div></div><span class="role-badge">Selected: ${escapeHtml(metadata.algorithm)}</span></div><div class="table-wrap"><table class="metrics-table"><thead><tr><th>Candidate</th><th>Accuracy</th><th>Precision</th><th>Recall</th><th>F1</th><th>ROC-AUC</th><th>PR-AUC</th></tr></thead><tbody>${comparisonRows}</tbody></table></div><div class="panel-body"><div class="field-hint">Selection rule: highest validation PR-AUC; ties use default recall, then ROC-AUC. Hyperparameters were tuned with three-fold cross-validation on the training portion. Metrics above are validation results; cards show the untouched test set.</div></div></section>
    <section class="panel"><div class="panel-head"><div><div class="panel-title">Held-out test confusion matrix</div><div class="panel-subtitle">Threshold ${metrics.classification_threshold} on the positive class</div></div></div><div class="panel-body"><table class="metrics-table"><thead><tr><th></th><th>Predicted paid</th><th>Predicted charged off</th></tr></thead><tbody><tr><td>Actual paid</td><td>${metrics.confusion_matrix.true_negative}</td><td>${metrics.confusion_matrix.false_positive}</td></tr><tr><td>Actual charged off</td><td>${metrics.confusion_matrix.false_negative}</td><td>${metrics.confusion_matrix.true_positive}</td></tr></tbody></table><div class="field-hint" style="margin-top:9px">Default probability is shown separately from this 0.5 binary threshold and from the configurable risk bands.</div></div></section></div>
    <div class="content-grid"><section class="panel"><div class="panel-head"><div><div class="panel-title">Permutation importance</div><div class="panel-subtitle">Change in held-out average precision when each feature is shuffled</div></div></div><div class="panel-body chart-bars">${importanceRows}</div></section><section class="panel"><div class="panel-head"><div><div class="panel-title">Training pipeline</div><div class="panel-subtitle">Reproducible preprocessing and model selection</div></div></div><div class="panel-body"><div class="timeline">${[['Raw cohort', `${metadata.dataset.rows_after_duplicate_id_removal} unique LendingClub records`],['Outcome filter', `${metadata.dataset.mature_rows_used} completed loans · Current and late accounts excluded`],['Preprocess', 'Median/mode imputation · one-hot categories · scaled numeric fields'],['Compare', 'Logistic regression · decision tree · random forest · SVM'],['Select & tune', 'Validation PR-AUC · 3-fold randomized search'],['Evaluate', `${metadata.split.held_out_test_rows} untouched stratified test records`]].map(([title,detail])=>`<div class="timeline-item"><strong>${escapeHtml(title)}</strong><small>${escapeHtml(detail)}</small></div>`).join('')}</div></div></section></div>
    <section class="panel"><div class="panel-head"><div><div class="panel-title">Model versions</div><div class="panel-subtitle">Previous artifacts remain available for prediction history</div></div></div><div class="table-wrap"><table class="data-table"><thead><tr><th>Version</th><th>Algorithm</th><th>Trained</th><th>State</th></tr></thead><tbody>${versionRows || '<tr><td colspan="4" class="empty-inline">No saved version records.</td></tr>'}</tbody></table></div></section>
    <div class="governance-note" style="margin-top:15px"><strong>Responsible AI.</strong> Protected demographics, location, free-text employment titles, identifiers, origination pricing/grade, and all post-origination payment, recovery, hardship, and settlement fields are excluded. The dataset does not contain sensitive demographic fields needed for a fairness audit. This model is for a demonstration and must not be the sole basis for lending.</div>`;
}

async function renderUsers() {
  const result = await api('/api/users');
  byId('page').innerHTML = `<div class="page-heading"><div><div class="eyebrow-sub">ACCESS CONTROL</div><h2>User access</h2><p>Applicant accounts can be promoted to analyst access or deactivated. Administrator access is managed on the server.</p></div></div>
    <section class="panel"><div class="panel-head"><div><div class="panel-title">Workspace accounts</div><div class="panel-subtitle">${result.items.length} accounts · list capped at 200</div></div></div><div class="table-wrap"><table class="data-table"><thead><tr><th>User</th><th>Email</th><th>Role</th><th>Joined</th><th>Access</th><th></th></tr></thead><tbody>${result.items.map((user)=>`<tr><td><div class="user-name-cell"><span class="small-avatar">${escapeHtml(user.full_name.split(/\s+/).slice(0,2).map((v)=>v[0]).join('').toUpperCase())}</span><span class="table-primary">${escapeHtml(user.full_name)}</span></div></td><td>${escapeHtml(user.email)}</td><td>${user.role === 'ADMIN' ? '<span class="role-badge">Administrator</span>' : `<select class="filter-select" data-user-role="${escapeHtml(user.id)}"><option value="APPLICANT" ${user.role==='APPLICANT'?'selected':''}>Applicant</option><option value="ANALYST" ${user.role==='ANALYST'?'selected':''}>Credit analyst</option></select>`}</td><td>${dateText(user.created_at)}</td><td>${user.active ? '<span class="status-badge status-approved">Active</span>' : '<span class="status-badge status-rejected">Inactive</span>'}</td><td>${user.role === 'ADMIN' || user.id === state.user.id ? '<span class="empty-inline">Protected</span>' : `<button class="button button-outline button-small" data-action="save-role" data-id="${escapeHtml(user.id)}">Save role</button><button class="button ${user.active?'button-danger':'button-primary'} button-small" data-action="toggle-user" data-id="${escapeHtml(user.id)}" data-active="${!user.active}">${user.active?'Deactivate':'Activate'}</button>`}</td></tr>`).join('') || '<tr><td colspan="6"><div class="empty-state">No user accounts.</div></td></tr>'}</tbody></table></div></section>
    <div class="callout warning" style="margin-top:14px">Changing a user's role grants access to sensitive applicant records. Only assign credit analyst access to authorized staff.</div>`;
}
async function renderPolicy() {
  const result = await api('/api/config/risk');
  const thresholds = result.thresholds;
  byId('page').innerHTML = `<div class="page-heading"><div><div class="eyebrow-sub">SYSTEM CONFIGURATION</div><h2>Risk thresholds</h2><p>These bands turn a probability into a display category. They do not make a lending decision.</p></div></div>
    <div class="content-grid equal"><section class="panel"><div class="panel-head"><div><div class="panel-title">Risk category bands</div><div class="panel-subtitle">Changes affect new predictions; saved assessments keep their original category.</div></div></div><div class="panel-body"><form id="threshold-form" class="field-grid"><div class="field"><label>Maximum LOW probability<span class="field-hint">Values up to this threshold are LOW risk.</span><input type="number" name="low_risk_maximum" min="0.01" max="0.99" step="0.01" value="${thresholds.low_risk_maximum}" required></label></div><div class="field"><label>Maximum MEDIUM probability<span class="field-hint">Values above LOW and up to this threshold are MEDIUM; higher values are HIGH.</span><input type="number" name="medium_risk_maximum" min="0.02" max="0.99" step="0.01" value="${thresholds.medium_risk_maximum}" required></label></div><div class="field-full"><button class="button button-primary" type="submit">Save thresholds</button></div></form></div></section>
    <section class="panel"><div class="panel-head"><div><div class="panel-title">Risk score mapping</div><div class="panel-subtitle">Probability of default × 100</div></div></div><div class="panel-body"><div class="detail-list"><div class="detail-field"><small>LOW</small><strong>0–${number(thresholds.low_risk_maximum*100,0)}%</strong></div><div class="detail-field"><small>MEDIUM</small><strong>${number(thresholds.low_risk_maximum*100,0)}–${number(thresholds.medium_risk_maximum*100,0)}%</strong></div><div class="detail-field"><small>HIGH</small><strong>${number(thresholds.medium_risk_maximum*100,0)}–100%</strong></div></div><div class="callout warning" style="margin-top:15px">Thresholds are configurable demonstration defaults, not calibrated credit policy. A category cannot approve or reject a loan.</div></div></section></div>
    <section class="panel"><div class="panel-head"><div><div class="panel-title">Deployment and privacy</div><div class="panel-subtitle">Operational configuration</div></div></div><div class="panel-body"><div class="detail-list"><div class="detail-field"><small>Database</small><strong>SQLite · persisted Docker volume</strong></div><div class="detail-field"><small>Transport</small><strong>HTTPS through Caddy for public domains</strong></div><div class="detail-field"><small>Sessions</small><strong>HTTP-only signed cookie · 8 hour expiry</strong></div><div class="detail-field"><small>Model service</small><strong>Local Python process · no external ML API</strong></div></div></div></section>`;
}
async function renderAudit() {
  const result = await api(`/api/audit?page=${state.auditPage}&page_size=50`);
  const pages = Math.max(1, Math.ceil(result.total / result.page_size));
  byId('page').innerHTML = `<div class="page-heading"><div><div class="eyebrow-sub">IMMUTABLE HISTORY</div><h2>Audit history</h2><p>Sign-ins, access changes, predictions, reviews, decisions, and policy changes.</p></div></div><section class="panel"><div class="panel-head"><div><div class="panel-title">${number(result.total)} audit events</div><div class="panel-subtitle">Newest first · 50 records per page</div></div></div><div class="table-wrap"><table class="data-table"><thead><tr><th>When</th><th>Actor</th><th>Action</th><th>Resource</th><th>Details</th></tr></thead><tbody>${result.items.map((item)=>`<tr><td>${dateText(item.created_at,true)}</td><td>${escapeHtml(item.actor_email || 'System')}<span class="table-secondary">${escapeHtml(item.actor_role || '')}</span></td><td><span class="role-badge">${escapeHtml(titleWords(item.action))}</span></td><td>${escapeHtml(item.resource_type)} ${escapeHtml((item.resource_id||'').slice(0,8))}</td><td><span class="audit-detail">${escapeHtml(JSON.stringify(item.details))}</span></td></tr>`).join('') || '<tr><td colspan="5" class="empty-inline">No events recorded.</td></tr>'}</tbody></table></div><div class="table-pagination"><span>Page ${result.page} of ${pages}</span><div class="pagination-actions"><button class="button button-outline button-small" data-action="audit-page" data-page-number="${Math.max(1,result.page-1)}" ${result.page<=1?'disabled':''}>Previous</button><button class="button button-outline button-small" data-action="audit-page" data-page-number="${Math.min(pages,result.page+1)}" ${result.page>=pages?'disabled':''}>Next</button></div></div></section>`;
}

function confirmModal(title, message, action, id, decision) {
  const backdrop = document.createElement('div');
  backdrop.className = 'modal-backdrop';
  backdrop.dataset.action = 'dismiss-modal';
  backdrop.innerHTML = `<section class="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title"><h3 id="modal-title">${escapeHtml(title)}</h3><p>${escapeHtml(message)}</p>${decision === 'NEEDS_MORE_INFORMATION' ? '<label class="field" style="margin-top:12px">What information is needed?<textarea id="decision-notes" maxlength="3000" required placeholder="Describe the documents or details requested"></textarea></label>' : decision ? '<label class="field" style="margin-top:12px">Analyst note (optional)<textarea id="decision-notes" maxlength="3000" placeholder="Add a concise review note"></textarea></label>' : ''}<div class="modal-actions"><button class="button button-outline" data-action="dismiss-modal">Cancel</button><button class="button ${decision==='REJECTED'?'button-danger':'button-primary'}" data-action="${action}" data-id="${escapeHtml(id)}" data-decision="${escapeHtml(decision||'')}">Continue</button></div></section>`;
  byId('workspace').appendChild(backdrop);
  backdrop.querySelector('.modal').addEventListener('click', (event) => event.stopPropagation());
}
function demoValues() {
  const sample = { loan_amount: 12000, term_months: 36, annual_income: 72000, home_ownership: 'RENT', purpose: 'debt_consolidation', dti: 18.5, prior_delinquencies: 0, fico_score: 710, recent_credit_inquiries: 1, open_accounts: 9, public_records: 0, revolving_balance: 8500, revolving_utilization: 28, total_accounts: 20 };
  Object.entries(sample).forEach(([key, value]) => { const field = byId('application-form')?.elements.namedItem(key); if (field) field.value = value; });
}

document.addEventListener('submit', async (event) => {
  const form = event.target;
  if (!(form instanceof HTMLFormElement)) return;
  event.preventDefault();
  clearNotice('auth-message');
  clearNotice();
  try {
    if (form.id === 'login-form') {
      const result = await api('/api/auth/login', { method: 'POST', body: JSON.stringify(formObject(form)) });
      state.user = result.user; state.page = 'dashboard'; showWorkspace();
    } else if (form.id === 'register-form') {
      const result = await api('/api/auth/register', { method: 'POST', body: JSON.stringify(formObject(form)) });
      state.user = result.user; state.page = 'dashboard'; showWorkspace();
    } else if (form.id === 'application-filter') {
      state.applicationPage = 1; await renderApplications();
    } else if (form.id === 'application-form') {
      state.busy = true;
      const button = form.querySelector('button[type="submit"]'); button.disabled = true; button.innerHTML = '<span class="spinner"></span> Assessing…';
      const data = formObject(form); const id = form.dataset.editId;
      const result = await api(id ? `/api/applications/${encodeURIComponent(id)}/information` : '/api/applications', { method: id ? 'PUT' : 'POST', body: JSON.stringify(data) });
      state.page = 'application-detail'; state.applicationId = result.id; await renderPage(); showNotice(id ? 'Updated information and assessment submitted.' : 'Application submitted and assessed.', 'success');
    } else if (form.id === 'threshold-form') {
      const data = formObject(form); await api('/api/config/risk', { method: 'PUT', body: JSON.stringify(data) }); await renderPolicy(); showNotice('Risk thresholds saved. Existing predictions were preserved.', 'success');
    }
  } catch (error) {
    if (form.id === 'login-form' || form.id === 'register-form') showNotice(error.message, 'error', 'auth-message');
    else showNotice(error.message, 'error');
  } finally { state.busy = false; }
});

document.addEventListener('click', async (event) => {
  const action = event.target.closest('[data-action]');
  if (!action) return;
  const name = action.dataset.action;
  try {
    if (name === 'show-register') { showRegister(); return; }
    if (name === 'show-login') { showAuth(); return; }
    if (name === 'nav') { navigate(action.dataset.page); return; }
    if (name === 'logout') { try { await api('/api/auth/logout', { method: 'POST' }); } catch {} state.user = null; showAuth(); return; }
    if (name === 'menu') { byId('sidebar').classList.toggle('open'); return; }
    if (name === 'fill-demo') { demoValues(); return; }
    if (name === 'open-app') { navigate('application-detail', { id: action.dataset.id }); return; }
    if (name === 'app-page') { state.applicationPage = Number(action.dataset.pageNumber); await renderApplications(); return; }
    if (name === 'audit-page') { state.auditPage = Number(action.dataset.pageNumber); await renderAudit(); return; }
    if (name === 'start-review') {
      await api(`/api/applications/${encodeURIComponent(action.dataset.id)}/review`, { method: 'POST' });
      await renderApplicationDetail(action.dataset.id); showNotice('Application moved to analyst review.', 'success'); return;
    }
    if (name === 'reassess') {
      await api(`/api/applications/${encodeURIComponent(action.dataset.id)}/predict`, { method: 'POST' });
      await renderApplicationDetail(action.dataset.id); showNotice('New prediction saved; earlier assessments remain in the history.', 'success'); return;
    }
    if (name === 'decision') {
      const decision = action.dataset.decision;
      const messages = { APPROVED: 'Record an approval after completing your review?', REJECTED: 'Record a rejection after completing your review?', NEEDS_MORE_INFORMATION: 'Ask the applicant to provide additional information?' };
      confirmModal(titleWords(decision), messages[decision], 'confirm-decision', action.dataset.id, decision); return;
    }
    if (name === 'confirm-decision') {
      const note = byId('decision-notes')?.value.trim() || '';
      const decision = action.dataset.decision;
      if (decision === 'NEEDS_MORE_INFORMATION' && !note) {
        const modal = action.closest('.modal');
        let feedback = modal.querySelector('.modal-feedback');
        if (!feedback) { feedback = document.createElement('div'); feedback.className = 'notice notice-error modal-feedback'; modal.insertBefore(feedback, modal.querySelector('.modal-actions')); }
        feedback.textContent = 'Please explain what information is needed.';
        byId('decision-notes')?.focus();
        return;
      }
      await api(`/api/applications/${encodeURIComponent(action.dataset.id)}/decision`, { method: 'POST', body: JSON.stringify({ decision, notes: note }) });
      byId('workspace').querySelector('.modal-backdrop')?.remove(); await renderApplicationDetail(action.dataset.id); showNotice('Analyst decision recorded in the audit history.', 'success'); return;
    }
    if (name === 'dismiss-modal') { action.closest('.modal-backdrop')?.remove(); return; }
    if (name === 'edit-application') {
      const item = await api(`/api/applications/${encodeURIComponent(action.dataset.id)}`);
      state.applicationId = item.id; state.page = 'new-application'; setPageHeader(); renderApplicationForm(item.application_data); return;
    }
    if (name === 'retrain') {
      confirmModal('Retrain the active model?', 'A new artifact will be trained from the bundled historical CSV. Previous prediction records will remain unchanged.', 'confirm-retrain', '', ''); return;
    }
    if (name === 'confirm-retrain') {
      action.disabled = true; action.textContent = 'Training…';
      await api('/api/models/retrain', { method: 'POST' }); byId('workspace').querySelector('.modal-backdrop')?.remove(); await renderModel(); showNotice('New model trained and activated from the supplied cohort.', 'success'); return;
    }
    if (name === 'save-role') {
      const role = document.querySelector(`[data-user-role="${CSS.escape(action.dataset.id)}"]`)?.value;
      await api(`/api/users/${encodeURIComponent(action.dataset.id)}/role`, { method: 'PATCH', body: JSON.stringify({ role }) }); await renderUsers(); showNotice('User role updated and recorded.', 'success'); return;
    }
    if (name === 'toggle-user') {
      await api(`/api/users/${encodeURIComponent(action.dataset.id)}/active`, { method: 'PATCH', body: JSON.stringify({ active: action.dataset.active === 'true' }) }); await renderUsers(); showNotice(action.dataset.active === 'true' ? 'User account activated.' : 'User account deactivated.', 'success'); return;
    }
  } catch (error) {
    const modal = action.closest('.modal');
    if (modal) {
      let feedback = modal.querySelector('.modal-feedback');
      if (!feedback) { feedback = document.createElement('div'); feedback.className = 'notice notice-error modal-feedback'; modal.insertBefore(feedback, modal.querySelector('.modal-actions')); }
      feedback.textContent = error.message;
      const retrainButton = modal.querySelector('[data-action="confirm-retrain"]');
      if (retrainButton) { retrainButton.disabled = false; retrainButton.textContent = 'Try again'; }
    } else showNotice(error.message, 'error');
  }
});

async function start() {
  try {
    const result = await api('/api/auth/me');
    state.user = result.user;
    state.page = 'dashboard';
    showWorkspace();
  } catch { state.user = null; showAuth(); }
}
start();
