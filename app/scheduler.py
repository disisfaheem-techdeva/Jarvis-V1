"""Scheduler — the time-based half of the Master Orchestrator.

The rulebook reacts to events; the scheduler reacts to *time*. On each tick it
scans every active tenant for work that has come due (e.g. a lead whose
follow-up time has arrived) and emits an event, which then flows through the
same rulebook as everything else. This is what lets the orchestrator chase
follow-ups and run periodic reports without a human remembering to.
"""


class Scheduler:
    def __init__(self, persist):
        self.p = persist

    def tick(self) -> int:
        """Run one scheduling pass across all active tenants. Returns work emitted."""
        emitted = 0
        for tenant_id in self.p.list_active_tenants():
            for lead in self.p.due_followups(tenant_id):
                self.p.emit(tenant_id, "lead.followup_due",
                            {"lead_id": str(lead["id"]),
                             "conversation_id": (str(lead["conversation_id"])
                                                 if lead.get("conversation_id") else None)},
                            "scheduler")
                self.p.clear_followup(tenant_id, lead["id"])   # dedupe: don't re-fire
                emitted += 1

        # THE REAPER - recover work Redis lost, before anything else.
        # A destructive BLPOP plus a worker crash used to strand a task at
        # 'running' forever, and a Redis restart orphaned every queued row.
        # Nothing noticed, because nothing was watching. Guarded by hasattr so a
        # persistence layer without the helper simply skips it.
        if hasattr(self.p, "requeue_lost_tasks"):
            for tenant_id in self.p.list_active_tenants():
                try:
                    out = self.p.requeue_lost_tasks(tenant_id)
                    if out["recovered"] or out["dead_lettered"]:
                        # Silent recovery is how an outage runs for 22 days.
                        self.p.emit(tenant_id, "ops.alert", {
                            "provider": "queue",
                            "message": (f"Queue recovery: {out['recovered']} task(s) "
                                        f"re-queued after a lost worker, "
                                        f"{out['dead_lettered']} dead-lettered.")},
                            "scheduler")
                        emitted += 1
                except Exception as e:
                    print(f"[scheduler] reaper skipped for {tenant_id}: {e}", flush=True)

        # Growth Engine: fire trial expiries so TechDeva can convert-or-drop.
        if hasattr(self.p, "due_trial_expiries"):
            for t in self.p.due_trial_expiries():
                self.p.emit(t["tenant_id"], "trial.expired",
                            {"tenant_id": t["tenant_id"]}, "scheduler")
                self.p.mark_trial_flagged(t["tenant_id"])       # dedupe: don't re-fire
                emitted += 1

        # Posts the owner approved for a later time.
        try:
            from .social import publish_due
            emitted += publish_due()
        except Exception as e:
            print(f"[scheduler] scheduled posts skipped: {e}", flush=True)

        # Ad autopilot (growth planner): auto-pause over the owner's limit, and
        # the weekly launch within the owner's daily cap. Throttled inside.
        try:
            from .adpilot import run_due as adpilot_due
            emitted += adpilot_due()
        except Exception as e:
            print(f"[scheduler] ad autopilot skipped: {e}", flush=True)

        # Reply checker: what each business's WhatsApp agent actually said (bot
        # errors, services or prices it does not have). Every 10 minutes.
        try:
            from .replycheck import run_due as replycheck_due
            emitted += replycheck_due()
        except Exception as e:
            print(f"[scheduler] reply checker skipped: {e}", flush=True)

        # Trial data retention: warn 3 days before, delete unpaid trials 15 days
        # after the trial ended (hourly inside).
        try:
            from .retention import run_due as retention_due
            emitted += retention_due()
        except Exception as e:
            print(f"[scheduler] trial data retention skipped: {e}", flush=True)

        # Oversight: ask the Optimizer (13) to review performance on a cadence.
        # It only ever PROPOSES; the human approval gate is unchanged. Guarded by
        # hasattr so a persistence layer without the helper simply skips this.
        if hasattr(self.p, "due_optimizations"):
            for t in self.p.due_optimizations():
                self.p.emit(t["tenant_id"], "optimization.due",
                            {"tenant_id": t["tenant_id"]}, "scheduler")
                self.p.mark_optimization_run(t["tenant_id"])    # dedupe: don't re-fire
                emitted += 1
        return emitted
