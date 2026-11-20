(() => {
  "use strict";
  const config = window.SUDOGEMIN_CONFIG || {};
  const $ = (selector) => document.querySelector(selector);
  const state = { token: sessionStorage.getItem("sudogemin_access_token") || "", claims: null, view: "overview", data: {} };
  const names = {overview:"Overview",marketplace:"Marketplace",datasets:"My datasets",transactions:"Transactions",workers:"AI workers",proofs:"Trust & proofs"};
  const apiBase = (config.apiBaseUrl || "").replace(/\/$/, "");
  const url = (path) => apiBase + path;
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const money = (value) => new Intl.NumberFormat("en-US",{style:"currency",currency:"USD"}).format(Number(value || 0));
  const date = (value) => value ? new Date(value).toLocaleDateString() : "—";
  const roles = () => new Set(state.claims?.roles || []);
  const hasRole = (...allowed) => roles().has("platform_admin") || allowed.some(role => roles().has(role));
  function decodeClaims(token) {
    try { return JSON.parse(atob(token.split(".")[1].replace(/-/g,"+").replace(/_/g,"/"))); } catch { return null; }
  }
  function notice(message, type="") {
    const el=$("#notice"); el.textContent=message; el.className="notice "+type; el.hidden=false;
    window.clearTimeout(notice.timer); notice.timer=window.setTimeout(()=>el.hidden=true,7000);
  }
  async function api(path, options={}) {
    const headers = new Headers(options.headers || {});
    headers.set("Accept","application/json");
    if (state.token) headers.set("Authorization","Bearer "+state.token);
    if (options.body && !(options.body instanceof FormData)) headers.set("Content-Type","application/json");
    const response=await fetch(url(path),{...options,headers});
    const body=await response.json().catch(()=>({}));
    if (!response.ok) throw new Error(body.detail || body.message || body.error || ("Request failed ("+response.status+")"));
    return body;
  }
  async function discover() {
    if (!config.oidcIssuer || !config.oidcClientId) throw new Error("OIDC is not configured. Set oidcIssuer and oidcClientId in frontend/config.js.");
    const issuer=config.oidcIssuer.replace(/\/$/,"");
    const response=await fetch(issuer+"/.well-known/openid-configuration");
    if (!response.ok) throw new Error("Could not load OIDC discovery metadata.");
    return response.json();
  }
  function randomString(size=64) {
    const bytes=new Uint8Array(size); crypto.getRandomValues(bytes);
    return Array.from(bytes,b=>("0"+b.toString(16)).slice(-2)).join("");
  }
  async function challenge(verifier) {
    const digest=await crypto.subtle.digest("SHA-256",new TextEncoder().encode(verifier));
    return btoa(String.fromCharCode(...new Uint8Array(digest))).replace(/\+/g,"-").replace(/\//g,"_").replace(/=+$/,"");
  }
  async function login() {
    const oidc=await discover(), verifier=randomString(), stateKey=randomString(24);
    sessionStorage.setItem("sudogemin_pkce_verifier",verifier);
    sessionStorage.setItem("sudogemin_oauth_state",stateKey);
    const params=new URLSearchParams({client_id:config.oidcClientId,response_type:"code",redirect_uri:config.oidcRedirectUri || location.href.split("?")[0],scope:config.oidcScopes || "openid profile email",state:stateKey,code_challenge:await challenge(verifier),code_challenge_method:"S256"});
    if(config.oidcAudience) params.set("audience",config.oidcAudience);
    location.assign(oidc.authorization_endpoint+"?"+params.toString());
  }
  async function handleCallback() {
    const params=new URLSearchParams(location.search), code=params.get("code");
    if(params.has("error")) throw new Error(params.get("error_description") || params.get("error"));
    if(!code) return false;
    if(params.get("state")!==sessionStorage.getItem("sudogemin_oauth_state")) throw new Error("Sign-in state validation failed.");
    const verifier=sessionStorage.getItem("sudogemin_pkce_verifier");
    if(!verifier) throw new Error("Sign-in session expired. Please try again.");
    const oidc=await discover(), body=new URLSearchParams({grant_type:"authorization_code",client_id:config.oidcClientId,code,redirect_uri:config.oidcRedirectUri || location.href.split("?")[0],code_verifier:verifier});
    const response=await fetch(oidc.token_endpoint,{method:"POST",headers:{"Content-Type":"application/x-www-form-urlencoded"},body});
    const result=await response.json();
    if(!response.ok || !result.access_token) throw new Error(result.error_description || "OIDC token exchange failed.");
    state.token=result.access_token; sessionStorage.setItem("sudogemin_access_token",state.token);
    sessionStorage.removeItem("sudogemin_pkce_verifier"); sessionStorage.removeItem("sudogemin_oauth_state");
    history.replaceState({},document.title,location.pathname);
    return true;
  }
  function signOut() { state.token="";state.claims=null;sessionStorage.removeItem("sudogemin_access_token");$("#app").hidden=true;$("#signin").hidden=false;$("#logout").hidden=true;$("#connection").textContent="Not connected"; }
  function setView(view) {
    state.view=view;$("#page-title").textContent=names[view] || "Overview";
    document.querySelectorAll(".nav-item").forEach(b=>b.classList.toggle("active",b.dataset.view===view));
    render().catch(e=>notice(e.message,"error"));
  }
  function table(headers, rows) {
    if(!rows.length) return '<div class="empty">Nothing here yet. New activity will appear when your organization starts using the exchange.</div>';
    return '<div class="table-wrap"><table><thead><tr>'+headers.map(h=>'<th>'+esc(h)+'</th>').join("")+'</tr></thead><tbody>'+rows.join("")+'</tbody></table></div>';
  }
  function pill(value) {
    const v=String(value||"unknown"), tone=/paid|published|approved|valid/i.test(v)?"green":/pending|created|review/i.test(v)?"amber":/failed|rejected|expired|invalid/i.test(v)?"red":"";
    return '<span class="pill '+tone+'">'+esc(v.replaceAll("_"," "))+'</span>';
  }
  function stat(label,value,foot) { return '<div class="stat"><div class="stat-label">'+esc(label)+'</div><div class="stat-value">'+esc(value)+'</div><div class="stat-foot">'+esc(foot)+'</div></div>'; }
  async function render() {
    const root=$("#view"); root.innerHTML='<div class="empty">Loading workspace…</div>';
    if(state.view==="overview") {
      const [d,t,m,a]=await Promise.all([api("/api/dashboard"),api("/api/trades"),api("/api/marketplace/listings"),api("/api/agents")]);
      state.data={...state.data,dashboard:d,trades:t.items||[],listings:m.items||[],agents:a};
      root.innerHTML='<div class="section-head"><div><h2>Good to see you.</h2><p>Your organization’s data exchange at a glance.</p></div><span class="pill green">Secure session</span></div><div class="stats">'+stat("Datasets in workspace",d.datasets_in_tenant,"Tenant-scoped inventory")+stat("Marketplace listings",d.published_marketplace_listings,"Approved and published")+stat("Transactions",d.trades_visible_to_tenant,"Buyer and seller activity")+stat("AI worker roles",a.count,"Execution: "+(a.execution_enabled?"enabled":"not configured"))+'</div><div class="columns"><div class="panel"><div class="panel-head"><h3>Recent transactions</h3><button class="secondary" data-go="transactions">View all</button></div>'+table(["Trade","Status","Payment","Amount"],(t.items||[]).slice(0,5).map(x=>'<tr><td>'+esc(x.id.slice(0,8))+'…</td><td>'+pill(x.status)+'</td><td>'+pill(x.payment_status)+'</td><td>'+money(x.amount_usd)+'</td></tr>'))+'</div><div class="panel"><h3>Governance status</h3><div class="worker"><div class="worker-icon">✓</div><div><b>Tenant isolation</b><small>API scopes private records to your organization.</small></div>'+pill("enforced")+'</div><div class="worker"><div class="worker-icon">◇</div><div><b>Cryptographic proofs</b><small>SHA-256 Merkle roots are attached to trades.</small></div>'+pill("available")+'</div><div class="worker"><div class="worker-icon">✋</div><div><b>Human approvals</b><small>Publication and payment require independent approval.</small></div>'+pill("required")+'</div></div></div>';
    } else if(state.view==="marketplace") {
      const data=await api("/api/marketplace/listings"); state.data.listings=data.items||[];
      root.innerHTML='<div class="section-head"><div><h2>Discover trusted data</h2><p>Only compliance-approved, published listings appear here.</p></div><span class="pill">'+data.count+' listings</span></div><div class="toolbar"><input id="market-search" placeholder="Search datasets, categories, descriptions…" aria-label="Search listings"></div><div id="listing-cards" class="cards" style="margin-top:16px"></div>';
      drawListings(state.data.listings);
      $("#market-search").addEventListener("input",e=>drawListings(state.data.listings.filter(x=>(x.name+" "+x.description+" "+x.category).toLowerCase().includes(e.target.value.toLowerCase()))));
    } else if(state.view==="datasets") {
      const data=await api("/api/datasets"); state.data.datasets=data.items||[];
      root.innerHTML='<div class="section-head"><div><h2>Your data assets</h2><p>Register dataset metadata. Compliance review and publication remain human-controlled.</p></div></div><div class="panel"><h3>Register a dataset</h3><form id="dataset-form" class="form-grid"><div class="field"><label>Dataset name</label><input name="name" required minlength="2" maxlength="120"></div><div class="field"><label>Category</label><input name="category" value="general" required></div><div class="field full"><label>Description</label><textarea name="description" maxlength="2000"></textarea></div><div class="field"><label>Source / provenance</label><input name="source" value="user-submitted"></div><div class="field"><label>Record count</label><input name="record_count" type="number" min="0" value="0"></div><div class="field full"><button class="primary">Submit for review</button></div></form></div><div class="panel"><h3>Registered datasets</h3>'+table(["Dataset","Category","Records","License","Status","Actions"],state.data.datasets.map(x=>'<tr><td><b>'+esc(x.name)+'</b><div class="muted">'+esc(x.id.slice(0,8))+'…</div></td><td>'+esc(x.category)+'</td><td>'+esc(x.record_count)+'</td><td>'+pill(x.license_status)+'</td><td>'+pill(x.status)+'</td><td><div class="actions">'+(hasRole("compliance","tenant_admin")&&x.status==="pending_review"?'<button class="secondary" data-review="'+esc(x.id)+'">Review</button>':"")+(hasRole("publisher","tenant_admin")&&x.license_status==="approved"&&x.status==="pending_review"?'<button class="primary" data-publish="'+esc(x.id)+'">Publish</button>':"")+'</div></td></tr>'))+'</div>';
      $("#dataset-form").addEventListener("submit",submitDataset);
      root.querySelectorAll("[data-review]").forEach(b=>b.addEventListener("click",()=>reviewDataset(b.dataset.review)));
      root.querySelectorAll("[data-publish]").forEach(b=>b.addEventListener("click",()=>publishDataset(b.dataset.publish)));
    } else if(state.view==="transactions") {
      const data=await api("/api/trades"); state.data.trades=data.items||[];
      root.innerHTML='<div class="section-head"><div><h2>Transaction center</h2><p>Independent approvals, verified payment events, and trade status.</p></div></div><div class="panel">'+table(["Trade","Dataset","Amount","Status","Payment","Created","Next action"],state.data.trades.map(x=>'<tr><td>'+esc(x.id.slice(0,8))+'…</td><td>'+esc(x.dataset_id.slice(0,8))+'…</td><td>'+money(x.amount_usd)+'</td><td>'+pill(x.status)+'</td><td>'+pill(x.payment_status)+'</td><td>'+date(x.created_at)+'</td><td><div class="actions">'+(hasRole("trade_approver","tenant_admin")&&x.status==="pending_approval"?'<button class="secondary" data-approve="'+esc(x.id)+'">Approve</button>':"")+(x.status==="approved_pending_payment"?'<button class="primary" data-checkout="'+esc(x.id)+'">Checkout</button>':"")+'<button class="secondary" data-verify="'+esc(x.id)+'">Verify proof</button></div></td></tr>'))+'</div>';
      root.querySelectorAll("[data-approve]").forEach(b=>b.addEventListener("click",()=>approveTrade(b.dataset.approve)));
      root.querySelectorAll("[data-checkout]").forEach(b=>b.addEventListener("click",()=>checkout(b.dataset.checkout)));
      root.querySelectorAll("[data-verify]").forEach(b=>b.addEventListener("click",()=>verify(b.dataset.verify)));
    } else if(state.view==="workers") {
      const a=await api("/api/agents"); state.data.agents=a;
      root.innerHTML='<div class="section-head"><div><h2>AI worker fleet</h2><p>Six defined worker responsibilities with governance boundaries.</p></div>'+pill(a.execution_enabled?"running":"not configured")+'</div><div class="callout">Worker execution is currently disabled by the backend. These are registered role definitions, not active autonomous processes. No AI provider, durable job queue, or worker runtime is configured. Human approval remains mandatory for consequential actions.</div><div class="panel" style="margin-top:16px"><h3>Worker registry</h3>'+((a.agents||[]).map((x,i)=>'<div class="worker"><div class="worker-icon">'+(i+1)+'</div><div><b>'+esc(x.name)+'</b><small>'+esc(x.role)+'</small></div>'+pill("defined only")+'</div>').join(""))+'</div>';
    } else if(state.view==="proofs") {
      root.innerHTML='<div class="section-head"><div><h2>Trust & verification</h2><p>Verify a trade’s SHA-256 Merkle root against its canonical proof fields.</p></div></div><div class="panel"><h3>Verify a transaction</h3><form id="verify-form" class="toolbar"><input name="trade_id" placeholder="Enter trade ID" minlength="36" required><button class="primary">Verify integrity</button></form><div id="proof-result" style="margin-top:16px"></div></div><div class="panel"><h3>Proof model</h3><p class="muted">Each trade proof commits to its trade ID, buyer and seller tenant IDs, dataset ID, approved amount, currency, and creation timestamp. The API recomputes the deterministic SHA-256 binary Merkle root when verification is requested.</p></div>';
      $("#verify-form").addEventListener("submit",async e=>{e.preventDefault();const id=new FormData(e.target).get("trade_id");await verify(id,true);});
    }
    root.querySelectorAll("[data-go]").forEach(b=>b.addEventListener("click",()=>setView(b.dataset.go)));
  }
  function drawListings(items) {
    const root=$("#listing-cards"); if(!items.length){root.innerHTML='<div class="empty">No matching published datasets yet.</div>';return;}
    root.innerHTML=items.map(x=>'<article class="card"><div class="card-meta"><span>'+esc(x.category)+'</span>'+pill("approved")+'</div><h3>'+esc(x.name)+'</h3><p>'+esc(x.description||"No description provided.")+'</p><div class="card-meta"><span>'+Number(x.record_count||0).toLocaleString()+' records</span><span>Seller '+esc(x.seller_tenant_id.slice(0,8))+'…</span></div><div class="proof">SHA-256 · '+esc(x.content_hash.slice(0,20))+'…</div><button class="primary" data-buy="'+esc(x.id)+'">Request purchase</button></article>').join("");
    root.querySelectorAll("[data-buy]").forEach(b=>b.addEventListener("click",()=>requestPurchase(b.dataset.buy)));
  }
  async function submitDataset(e) {
    e.preventDefault();const f=new FormData(e.target);
    try { await api("/api/datasets",{method:"POST",body:JSON.stringify({name:f.get("name"),description:f.get("description"),category:f.get("category"),source:f.get("source"),record_count:Number(f.get("record_count")||0)})});notice("Dataset submitted for compliance review.","success");await render(); } catch(err){notice(err.message,"error");}
  }
  async function reviewDataset(id) {
    const decision=prompt("Enter approved or rejected:","approved");if(!decision)return;
    const reason=prompt("Provide the compliance review reason:");if(!reason)return;
    try{await api("/api/datasets/"+id+"/review",{method:"POST",body:JSON.stringify({decision:decision.toLowerCase(),reason})});notice("Review recorded.","success");await render();}catch(e){notice(e.message,"error");}
  }
  async function publishDataset(id) {
    if(!confirm("Publish this compliance-approved listing to the marketplace?"))return;
    try{await api("/api/datasets/"+id+"/publish",{method:"POST"});notice("Listing published.","success");await render();}catch(e){notice(e.message,"error");}
  }
  async function requestPurchase(id) {
    const amount=prompt("Enter the agreed purchase amount in USD:");if(!amount)return;
    if(!/^\d+(\.\d{1,2})?$/.test(amount)||Number(amount)<=0){notice("Enter a valid positive USD amount.","error");return;}
    try{const trade=await api("/api/trades",{method:"POST",body:JSON.stringify({dataset_id:id,amount_usd:amount})});notice("Purchase request created. An independent trade approver must approve it before checkout.","success");setView("transactions");}catch(e){notice(e.message,"error");}
  }
  async function approveTrade(id) {
    if(!confirm("Approve this trade? You cannot approve a trade you created."))return;
    try{await api("/api/trades/"+id+"/approve",{method:"POST"});notice("Trade independently approved. Payment can now be initiated.","success");await render();}catch(e){notice(e.message,"error");}
  }
  async function checkout(id) {
    try{const result=await api("/api/trades/"+id+"/checkout",{method:"POST"});if(!result.checkout_url)throw new Error("Checkout URL was not returned.");location.assign(result.checkout_url);}catch(e){notice(e.message,"error");}
  }
  async function verify(id,inline=false) {
    try{const r=await api("/api/verify/"+encodeURIComponent(id));const markup='<div class="worker"><div class="worker-icon">'+(r.valid?"✓":"!")+'</div><div><b>Integrity '+(r.valid?"verified":"check failed")+'</b><small>'+esc(r.algorithm)+' · '+esc(date(r.verified_at))+'</small><div class="proof">'+esc(r.merkle_root)+'</div></div>'+pill(r.valid?"valid":"invalid")+'</div>';
      if(inline)$("#proof-result").innerHTML=markup;else notice("Trade proof "+(r.valid?"is valid.":"did not validate."),r.valid?"success":"error");
    }catch(e){if(inline)$("#proof-result").textContent=e.message;else notice(e.message,"error");}
  }
  async function start() {
    $("#year").textContent=new Date().getFullYear();
    $("#login").addEventListener("click",()=>login().catch(e=>notice(e.message,"error")));
    $("#logout").addEventListener("click",signOut);
    $("#navigation").addEventListener("click",e=>{const b=e.target.closest("[data-view]");if(b&&state.token)setView(b.dataset.view);});
    try { await handleCallback(); } catch(e){notice(e.message,"error");}
    if(state.token) {
      state.claims=decodeClaims(state.token);
      if(!state.claims || Number(state.claims.exp||0)*1000<Date.now()){signOut();notice("Your sign-in session expired. Please sign in again.","error");return;}
      $("#signin").hidden=true;$("#app").hidden=false;$("#logout").hidden=false;$("#connection").textContent="Authenticated";$("#account").textContent=(state.claims.name||state.claims.email||"?").slice(0,1).toUpperCase();
      setView("overview");
    } else {
      $("#auth-help").textContent=config.oidcIssuer&&config.oidcClientId?"Secure OIDC sign-in is configured. Your provider validates your identity; the API enforces tenant access.":"Set oidcIssuer and oidcClientId in frontend/config.js. No demo credentials or authentication bypass are provided.";
    }
  }
  start();
})();