import { Amplify } from "aws-amplify";
import { initPlatform } from "./platform.js";
import { signIn as cognitoSignIn, signUp as cognitoSignUp, confirmSignUp, signOut, getCurrentUser, fetchAuthSession } from "aws-amplify/auth";

Amplify.configure({Auth:{Cognito:{userPoolId:"us-east-2_yeNn9tWY7",userPoolClientId:"5kl6i9mong804ft96sp5c4fdm1",loginWith:{email:true}}}});
const $=id=>document.getElementById(id);
let pendingEmail="";
function status(id,message,type=""){const el=$(id);el.textContent=message;el.className="status "+type}
function showAuth(mode){$("signinForm").classList.toggle("hidden",mode!=="signin");$("signupForm").classList.toggle("hidden",mode!=="signup");$("confirmForm").classList.add("hidden");$("showSignIn").classList.toggle("primary",mode==="signin");$("showSignUp").classList.toggle("primary",mode==="signup");$("authTitle").textContent=mode==="signin"?"Access your workspace":"Create your workspace"}
$("showSignIn").onclick=()=>showAuth("signin");$("showSignUp").onclick=()=>showAuth("signup");
async function token(){const session=await fetchAuthSession();const t=session.tokens?.idToken;if(!t)throw Error("Sign in is required.");return t.toString()}
async function api(path,options={}){const t=await token();const headers={...(options.headers||{}),Authorization:"Bearer "+t};if(options.body)headers["Content-Type"]="application/json";const res=await fetch(path,{...options,headers});const data=await res.json().catch(()=>({detail:"Invalid server response"}));if(!res.ok)throw Error(data.detail||data.error||"Request failed ("+res.status+")");return data}
function escapeHtml(s){return String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]))}
async function signedIn(){ $("auth").classList.add("hidden");$("app").classList.remove("hidden");$("logout").classList.remove("hidden");await refresh();await initPlatform({api,status,escapeHtml})}
$("signUp").onclick=async()=>{const email=$("signupEmail").value.trim(),password=$("signupPassword").value;try{pendingEmail=email;await cognitoSignUp({username:email,password,options:{userAttributes:{email}}});$("signupForm").classList.add("hidden");$("confirmForm").classList.remove("hidden");status("authStatus","Check your email for the verification code.","good")}catch(e){status("authStatus",e.message,"error")}};
$("confirmSignUp").onclick=async()=>{try{await confirmSignUp({username:pendingEmail,confirmationCode:$("confirmCode").value.trim()});status("authStatus","Email verified. Sign in to continue.","good");$("loginEmail").value=pendingEmail;showAuth("signin")}catch(e){status("authStatus",e.message,"error")}};
$("signIn").onclick=async()=>{try{await cognitoSignIn({username:$("loginEmail").value.trim(),password:$("loginPassword").value});await signedIn()}catch(e){status("authStatus",e.message,"error")}};
$("logout").onclick=async()=>{await signOut();$("app").classList.add("hidden");$("auth").classList.remove("hidden");$("logout").classList.add("hidden");status("authStatus","Signed out.")};
function renderAutonomy(controls){
 const pause=$("aiGlobalPause");
 pause.checked=!!controls.paused;
 pause.disabled=!!controls.global_pause;
 const modes=controls.modes||{};
 $("autonomyAgentControls").innerHTML=["ingestion","quality","scoping","marketplace","sales","governance"].map(id=>{
  const label=({ingestion:"Ingestion",quality:"Data quality",scoping:"Customer scoping",marketplace:"Marketplace",sales:"Sales",governance:"Governance"})[id];
  const mode=modes[id]||"human_in_loop";
  return '<article class="agent"><b>'+label+'</b><span>Execution policy</span><select data-agent-mode="'+id+'" style="margin-top:8px"><option value="human_in_loop" '+(mode==="human_in_loop"?"selected":"")+'>Human in the loop</option><option value="autonomous" '+(mode==="autonomous"?"selected":"")+'>AI autonomous</option></select></article>';
 }).join("");
 if(controls.global_pause)status("autonomyStatus","A platform emergency pause is active. Tenant controls cannot override it.","error");
 else status("autonomyStatus",controls.paused?"All agents paused.":"Controls loaded.");
}
$("saveAutonomy").onclick=async()=>{
 const button=$("saveAutonomy");button.disabled=true;
 try{
  const modes={};
  document.querySelectorAll("[data-agent-mode]").forEach(el=>{modes[el.dataset.agentMode]=el.value});
  const result=await api("/api/agents/controls",{method:"PUT",body:JSON.stringify({paused:$("aiGlobalPause").checked,modes})});
  renderAutonomy(result.controls);
  status("autonomyStatus","Autonomy settings saved.","good");
 }catch(e){status("autonomyStatus",e.message,"error")}
 finally{button.disabled=false}
};
async function refresh(){status("apiStatus","Refreshing workspace…");try{const [dash,agents,integrations,datasets]=await Promise.all([api("/api/dashboard"),api("/api/agents"),api("/api/integrations/status"),api("/api/datasets")]);$("agentCount").textContent=dash.agents;$("datasetCount").textContent=dash.datasets;$("tradeCount").textContent=dash.trades;$("quantumStatus").textContent=integrations.cuda_q.status;renderAutonomy(agents.controls||{modes:{},paused:false});$("agents").innerHTML=agents.agents.map(a=>'<article class="agent"><b>'+escapeHtml(a.name)+'</b><span>'+escapeHtml(a.role)+'</span></article>').join("");$("integrations").innerHTML='<div class="dataset"><b>Stripe</b><small>'+escapeHtml(integrations.stripe.status)+' · checkout '+(integrations.stripe.checkout_enabled?"enabled":"disabled")+'</small></div><div class="dataset"><b>CUDA-Q</b><small>'+escapeHtml(integrations.cuda_q.status)+' · '+escapeHtml(integrations.cuda_q.backend)+'</small></div>';renderDatasets(datasets.items);status("apiStatus","Workspace loaded.","good")}catch(e){status("apiStatus",e.message,"error")}}
function renderDatasets(items){$("datasets").innerHTML=items.length?items.map(d=>'<div class="dataset"><b>'+escapeHtml(d.name)+'</b> <span class="badge">'+escapeHtml(d.license_status||d.status||"pending_review")+'</span><small>'+escapeHtml(d.category||"general")+' · '+Number(d.record_count||0).toLocaleString()+' declared records</small><small>'+escapeHtml(d.description||"No description")+'</small></div>').join(""):'No datasets submitted yet.'}
$("refresh").onclick=refresh;
$("createDataset").onclick=async()=>{const b=$("createDataset");b.disabled=true;try{const item={name:$("datasetName").value.trim(),description:$("datasetDescription").value,category:$("datasetCategory").value.trim()||"general",source:$("datasetSource").value.trim()||"user-submitted",license_evidence_url:$("licenseEvidenceUrl").value.trim(),permitted_use:$("permittedUse").value.trim(),record_count:Number($("recordCount").value||0)};const created=await api("/api/datasets",{method:"POST",body:JSON.stringify(item)});status("datasetStatus","Submitted. Status: pending review.","good");$("datasetName").value="";await refresh()}catch(e){status("datasetStatus",e.message,"error")}finally{b.disabled=false}};
$("connection").textContent="AWS API · "+location.host;
try{await getCurrentUser();await signedIn()}catch{showAuth("signin")}