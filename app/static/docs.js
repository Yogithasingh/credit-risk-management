const root = document.getElementById('endpoints');
const state = document.getElementById('api-state');
const order = ['get', 'post', 'put', 'patch', 'delete'];

function endpointCard(path, method, operation) {
  const card = document.createElement('article');
  card.className = 'endpoint';
  const top = document.createElement('div');
  top.className = 'endpoint-top';
  const verb = document.createElement('span');
  verb.className = `verb ${method}`;
  verb.textContent = method.toUpperCase();
  const url = document.createElement('code');
  url.textContent = path;
  const summary = document.createElement('strong');
  summary.textContent = operation.summary || operation.operationId || 'API endpoint';
  top.append(verb, url, summary);
  card.append(top);
  if (operation.description) {
    const detail = document.createElement('p');
    detail.textContent = operation.description;
    card.append(detail);
  }
  const auth = document.createElement('small');
  auth.className = 'auth-note';
  auth.textContent = ['/api/auth/login', '/api/auth/register', '/api/auth/me', '/api/auth/logout', '/api/health'].includes(path)
    ? 'Authentication: public or session endpoint'
    : 'Authentication: signed-in session cookie; role restrictions apply';
  card.append(auth);
  return card;
}

fetch('/openapi.json', { credentials: 'same-origin' })
  .then((response) => {
    if (!response.ok) throw new Error('Could not load the OpenAPI definition.');
    return response.json();
  })
  .then((schema) => {
    const endpoints = Object.entries(schema.paths || {}).flatMap(([path, methods]) =>
      Object.entries(methods)
        .filter(([method]) => order.includes(method))
        .map(([method, operation]) => ({ path, method, operation }))
    ).sort((left, right) => left.path.localeCompare(right.path) || order.indexOf(left.method) - order.indexOf(right.method));
    for (const endpoint of endpoints) root.append(endpointCard(endpoint.path, endpoint.method, endpoint.operation));
    state.textContent = `${endpoints.length} endpoints · schema generated from the running API`;
  })
  .catch((error) => { state.textContent = error.message; });
