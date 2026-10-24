"""The Autopilot Gallery's work for the Content (02) and Publishing (05) agents.

Same agent names as the existing ones, so the owner's on/off switches, the
usage meter and each agent's memory scope apply unchanged; these classes only
take the new task types (registry lookup is by task type).

  content     content.analyze_media   look at each picture, tag it with one configured service
              content.caption_batch   write the campaign's captions (drafts)
              content.recaption_one   write one post's caption again
  publishing  publish_post            publish one approved post at its time

The content tasks only read: what they made travels in an event and the
orchestrator writes it as drafts (rule -> marketing_apply). human_review is
not set on them: the drafts already wait for the campaign approval, which is
the person's decision recorded on each post; parking the task as well would
add a second approval card that approves nothing.

publish_post is the publishing seam: it claims the post and calls Meta itself
(app/marketing/publisher.py), and it refuses any post without a recorded
human approval. Its outcome events (published, ops.alert, reconnect) go
through the orchestrator like any other.
"""
from .contract import Agent, AgentResult


class GalleryContentAgent(Agent):
    name = "content"
    TYPES = {"content.analyze_media", "content.caption_batch", "content.recaption_one"}

    def handles(self, t):
        return t in self.TYPES

    def run(self, task, ctx):
        from ..marketing import captioner
        p = task.payload
        if task.type == "content.analyze_media":
            out = captioner.analyze(task.tenant_id, p["campaign_id"])
            return AgentResult(status="done", result={"analyzed": len(out["assets"])},
                               events=[{"type": "marketing.media_analyzed", "payload": out}])
        if task.type == "content.caption_batch":
            out = captioner.caption_batch(task.tenant_id, p["campaign_id"])
            return AgentResult(status="done",
                               result={"captions": len(out["captions"]), "failed": out["failed"]},
                               events=[{"type": "marketing.captions_ready", "payload": out}])
        out = captioner.recaption_one(task.tenant_id, p["post_id"])
        return AgentResult(status="done", result={"post_id": p["post_id"]},
                           events=[{"type": "marketing.caption_rewritten", "payload": out}])


class GalleryPublishingAgent(Agent):
    name = "publishing"

    def handles(self, t):
        return t == "publish_post"

    def run(self, task, ctx):
        from ..marketing.publisher import publish_one
        out = publish_one(task.tenant_id, task.payload["post_id"])
        events = out.pop("events")
        return AgentResult(status="done", result=out, events=events)
