import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

Deno.serve(async (request) => {
  const expected = Deno.env.get("TELEGRAM_WEBHOOK_SECRET");
  const supplied = request.headers.get("x-telegram-bot-api-secret-token");
  if (!expected || supplied !== expected) return new Response("unauthorized", { status: 401 });

  const update = await request.json();
  const supabase = createClient(
    Deno.env.get("SUPABASE_URL")!,
    Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
  );
  const { error } = await supabase.from("telegram_update_inbox").upsert(
    { update_id: update.update_id, payload: update },
    { onConflict: "update_id", ignoreDuplicates: true },
  );
  if (error) return new Response(error.message, { status: 500 });

  const dispatchUrl = Deno.env.get("PROCESSOR_DISPATCH_URL");
  const dispatchToken = Deno.env.get("PROCESSOR_DISPATCH_TOKEN");
  if (dispatchUrl && dispatchToken) {
    const dispatch = await fetch(dispatchUrl, {
      method: "POST",
      headers: { Authorization: `Bearer ${dispatchToken}`, "Content-Type": "application/json" },
      body: JSON.stringify({ event_type: "telegram-update" }),
    });
    if (!dispatch.ok) {
      await supabase.from("telegram_update_inbox").update({
        last_error: `processor dispatch failed: ${dispatch.status}`,
      }).eq("update_id", update.update_id);
      console.error("processor dispatch failed", dispatch.status);
    }
  }
  return new Response("ok");
});
