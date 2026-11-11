export async function initPlatform({api,status,escapeHtml}) {
  const $ = (id) => document.getElementById(id);
  if (window.__sudogeminiPlatformReady) return;
  window.__sudogeminiPlatformReady = true;

  async function refreshReviewQueue() {
    try {
      const [datasets, listings] = await Promise.all([
        api("/api/datasets/review-queue"),
        api("/api/marketplace/review-queue")
      ]);
      const ds = datasets.items.map(x =>
        '<div class="dataset"><b>'+escapeHtml(x.name||x.record_id)+'</b><small>'+escapeHtml(x.license_evidence_url||"No evidence URL")+'</small><small>'+escapeHtml(x.permitted_use||"No permitted-use statement")+'</small><button class="btn" data-approve-dataset="'+escapeHtml(x.record_id)+'">Approve dataset rights</button></div>'
      ).join("");
      const ls = listings.items.map(x =>
        '<div class="dataset"><b>'+escapeHtml(x.title||x.listing_id)+'</b><small>$'+Number(x.price_usd||0).toFixed(2)+' · '+escapeHtml(x.license_summary||"")+'</small><button class="btn" data-approve-listing="'+escapeHtml(x.listing_id)+'">Approve marketplace listing</button></div>'
      ).join("");
      $("reviewQueue").innerHTML = '<h3>Dataset rights</h3>'+(ds||"No pending datasets.")+'<h3>Marketplace listings</h3>'+(ls||"No pending listings.");
    } catch (e) {
      $("reviewQueue").textContent = "Administrator review access unavailable: "+e.message;
    }
  }

  async function refreshMarketplace() {
    try {
      const [catalog, orders] = await Promise.all([
        api("/api/marketplace/listings"), api("/api/trades")
      ]);
      $("marketCatalog").innerHTML = catalog.items.length ? catalog.items.map(x =>
        '<div class="dataset"><b>'+escapeHtml(x.title)+'</b><small>'+escapeHtml(x.category)+' · $'+Number(x.price_usd).toFixed(2)+'</small><small>'+escapeHtml(x.license_summary)+'</small><button class="btn" data-buy="'+escapeHtml(x.listing_id)+'">Accept license and request purchase</button></div>'
      ).join("") : "No approved listings yet.";
      $("orders").innerHTML = orders.items.length ? orders.items.map(x =>
        '<div class="dataset"><b>Order '+escapeHtml(x.id)+'</b><small>'+escapeHtml(x.role||"buyer")+' · '+escapeHtml(x.status)+' · $'+Number(x.amount_usd||0).toFixed(2)+'</small>'+
        ((x.role==="seller"&&x.status==="pending_seller_approval")?'<button class="btn" data-approve-order="'+escapeHtml(x.id)+'">Approve order</button>':"")+
        ((x.role!=="seller"&&x.status==="approved_pending_payment")?'<button class="btn" data-checkout="'+escapeHtml(x.id)+'">Pay securely</button>':"")+
        ((x.role!=="seller"&&x.status==="paid_pending_fulfillment")?'<button class="btn" data-download="'+escapeHtml(x.id)+'">Download dataset</button>':"")+
        '</div>'
      ).join("") : "No orders yet.";
    } catch (e) {
      $("marketCatalog").textContent = e.message;
      $("orders").textContent = "Orders unavailable: "+e.message;
    }
  }

  $("reviewQueue").addEventListener("click", async (e) => {
    const d = e.target?.dataset || {};
    try {
      if (d.approveDataset) await api("/api/datasets/"+encodeURIComponent(d.approveDataset)+"/approve",{method:"POST"});
      if (d.approveListing) await api("/api/marketplace/listings/"+encodeURIComponent(d.approveListing)+"/approve",{method:"POST"});
      await refreshReviewQueue();
    } catch (err) { $("reviewQueue").textContent = err.message; }
  });

  $("runAgent").onclick = async () => {
    try {
      const result = await api("/api/agents/run",{method:"POST",body:JSON.stringify({
        agent_id:$("agentRole").value, task:$("agentTask").value.trim()
      })});
      status("agentTaskStatus","Queued job "+result.job_id+". The worker must be running and Bedrock configured.","good");
    } catch(e) { status("agentTaskStatus",e.message,"error"); }
  };

  $("uploadDataset").onclick = async () => {
    const file=$("datasetFile").files[0], id=$("uploadDatasetId").value.trim();
    if(!file||!id){status("uploadStatus","Choose a file and enter an approved dataset ID.","error");return;}
    try {
      const ext=file.name.split(".").pop().toLowerCase();
      const type=file.type||({csv:"text/csv",json:"application/json",jsonl:"application/x-ndjson",ndjson:"application/x-ndjson"}[ext]||"");
      const grant=await api("/api/ingestion/"+encodeURIComponent(id)+"/upload-url",{
        method:"POST",body:JSON.stringify({filename:file.name,content_type:type})
      });
      const uploaded=await fetch(grant.upload_url,{method:"PUT",headers:grant.required_headers,body:file});
      if(!uploaded.ok) throw Error("Private S3 upload failed ("+uploaded.status+").");
      const done=await api("/api/ingestion/"+encodeURIComponent(id)+"/complete?object_key="+encodeURIComponent(grant.object_key),{method:"POST"});
      status("uploadStatus","Upload complete. Validation job "+done.job_id+" queued.","good");
    } catch(e) { status("uploadStatus",e.message,"error"); }
  };

  $("submitListing").onclick = async () => {
    try {
      const result=await api("/api/marketplace/listings",{method:"POST",body:JSON.stringify({
        dataset_id:$("listingDatasetId").value.trim(),
        price_usd:Number($("listingPrice").value),
        license_summary:$("listingLicense").value.trim()
      })});
      status("listingStatus","Listing submitted for administrator review: "+result.listing_id,"good");
    } catch(e) { status("listingStatus",e.message,"error"); }
  };

  $("sellerOnboarding").onclick = async () => {
    try {
      const result=await api("/api/marketplace/seller/onboarding",{method:"POST",body:JSON.stringify({
        customer_email:$("sellerEmail").value.trim()
      })});
      window.location.assign(result.onboarding_url);
    } catch(e) { status("sellerStatus",e.message,"error"); }
  };

  $("marketCatalog").addEventListener("click",async(e)=>{
    const id=e.target?.dataset?.buy;if(!id)return;
    try {
      const result=await api("/api/trades",{method:"POST",body:JSON.stringify({listing_id:id,accept_license:true})});
      status("sellerStatus","Order "+result.trade_id+" created. Awaiting seller approval.","good");
      await refreshMarketplace();
    } catch(err) { status("sellerStatus",err.message,"error"); }
  });

  $("orders").addEventListener("click",async(e)=>{
    const d=e.target?.dataset||{};
    try {
      if(d.approveOrder){await api("/api/trades/"+encodeURIComponent(d.approveOrder)+"/seller-approve",{method:"POST"});await refreshMarketplace();}
      if(d.checkout){const r=await api("/api/trades/"+encodeURIComponent(d.checkout)+"/checkout",{method:"POST"});window.location.assign(r.checkout_url);}
      if(d.download){const r=await api("/api/trades/"+encodeURIComponent(d.download)+"/download");window.location.assign(r.download_url);}
    } catch(err) { status("sellerStatus",err.message,"error"); }
  });

  $("refreshMarket").onclick=refreshMarketplace;
  await Promise.all([refreshReviewQueue(),refreshMarketplace()]);
}
