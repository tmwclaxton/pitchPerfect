import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from profile_scraper import extract_profile_fields_from_nodes
from ui_dump import composer_draft_texts, is_composer_node, parse_ui_nodes
from your_turn import _parse_messages_from_nodes


CHAT_WITH_DRAFT_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" resource-id="" class="android.widget.FrameLayout"
    package="co.hinge.app" content-desc="" bounds="[0,0][1280,2800]">
    <node index="0" text="" resource-id="" class="android.view.ViewGroup"
      package="co.hinge.app" content-desc="You: hey sara" clickable="false"
      bounds="[40,400][600,480]"/>
    <node index="1" text="" resource-id="" class="android.view.ViewGroup"
      package="co.hinge.app" content-desc="Sara: hi there" clickable="false"
      bounds="[40,500][600,580]"/>
    <node index="2" text="No worries, busy days call for better evenings. How about a low-key bar near Marylebone around 8?"
      resource-id="co.hinge.app:id/messageComposition"
      class="android.widget.EditText" package="co.hinge.app"
      content-desc="" clickable="true" editable="true"
      bounds="[40,2500][1000,2700]"/>
    <node index="3" text="Send a message"
      resource-id="" class="android.widget.TextView" package="co.hinge.app"
      content-desc="" bounds="[40,2500][400,2550]"/>
  </node>
</hierarchy>
"""

PROFILE_WITH_DRAFT_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" resource-id="" class="android.widget.FrameLayout"
    package="co.hinge.app" content-desc="" bounds="[0,0][1280,2800]">
    <node index="0" text="" resource-id="" class="android.view.ViewGroup"
      package="co.hinge.app" content-desc="Age" bounds="[40,300][200,360]"/>
    <node index="1" text="23" resource-id="" class="android.widget.TextView"
      package="co.hinge.app" content-desc="" bounds="[220,300][300,360]"/>
    <node index="2" text="I never stay where I’ve outgrown."
      resource-id="" class="android.widget.TextView" package="co.hinge.app"
      content-desc="" bounds="[40,500][900,600]"/>
    <node index="3" text="easier on whatsapp if you want"
      resource-id="co.hinge.app:id/messageComposition"
      class="android.widget.EditText" package="co.hinge.app"
      content-desc="" clickable="true" editable="true"
      bounds="[40,2500][1000,2700]"/>
  </node>
</hierarchy>
"""


class ComposerFilterTest(unittest.TestCase):
    def test_chat_history_ignores_composer_draft(self):
        nodes = parse_ui_nodes(CHAT_WITH_DRAFT_XML)
        self.assertTrue(any(is_composer_node(n) for n in nodes))
        drafts = composer_draft_texts(nodes)
        self.assertTrue(any("marylebone" in d.lower() for d in drafts))

        messages, _ = _parse_messages_from_nodes(nodes)
        texts = [m.text.lower() for m in messages]
        self.assertIn("hey sara", texts)
        self.assertIn("hi there", texts)
        self.assertFalse(any("marylebone" in t for t in texts))
        self.assertFalse(any("send a message" in t for t in texts))

    def test_profile_scrape_ignores_composer_draft(self):
        nodes = parse_ui_nodes(PROFILE_WITH_DRAFT_XML)
        fields = extract_profile_fields_from_nodes(nodes, match_name="Sara")
        texts = {f.text_content.lower() for f in fields}
        self.assertIn("23", texts)
        self.assertTrue(any("outgrown" in t for t in texts))
        self.assertFalse(any("whatsapp" in t for t in texts))
        self.assertFalse(any("send a message" in t for t in texts))


if __name__ == "__main__":
    unittest.main()
