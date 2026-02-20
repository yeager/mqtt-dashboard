"""MQTT Dashboard - Real-time MQTT monitoring with GTK4/Adwaita."""
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, Gio, Gdk
import json
import os
import threading
import gettext
from datetime import datetime
from collections import deque

try:
    import paho.mqtt.client as mqtt
    HAS_MQTT = True
except ImportError:
    HAS_MQTT = False

_ = gettext.gettext
APP_ID = "io.github.yeager.MqttDashboard"
CONFIG_FILE = os.path.expanduser("~/.config/mqtt-dashboard/config.json")


class SparklineWidget(Gtk.DrawingArea):
    """Simple sparkline chart."""
    def __init__(self, max_points=50):
        super().__init__()
        self.values = deque(maxlen=max_points)
        self.set_content_width(200)
        self.set_content_height(60)
        self.set_draw_func(self._draw)

    def add_value(self, val):
        try:
            self.values.append(float(val))
        except (ValueError, TypeError):
            return
        self.queue_draw()

    def _draw(self, area, cr, width, height):
        if len(self.values) < 2:
            return
        vals = list(self.values)
        min_v, max_v = min(vals), max(vals)
        rng = max_v - min_v if max_v != min_v else 1
        n = len(vals)
        cr.set_source_rgba(0.2, 0.6, 0.9, 0.8)
        cr.set_line_width(2)
        for i, v in enumerate(vals):
            x = i * width / (n - 1)
            y = height - ((v - min_v) / rng) * (height - 4) - 2
            if i == 0:
                cr.move_to(x, y)
            else:
                cr.line_to(x, y)
        cr.stroke()


class GaugeWidget(Gtk.DrawingArea):
    """Simple gauge 0-100."""
    def __init__(self):
        super().__init__()
        self.value = 0
        self.set_content_width(80)
        self.set_content_height(50)
        self.set_draw_func(self._draw)

    def set_value(self, val):
        try:
            self.value = max(0, min(100, float(val)))
        except (ValueError, TypeError):
            return
        self.queue_draw()

    def _draw(self, area, cr, width, height):
        import math
        cx, cy = width / 2, height - 5
        r = min(cx, cy) - 5
        # Background arc
        cr.set_source_rgba(0.5, 0.5, 0.5, 0.3)
        cr.set_line_width(8)
        cr.arc(cx, cy, r, math.pi, 2 * math.pi)
        cr.stroke()
        # Value arc
        cr.set_source_rgba(0.2, 0.7, 0.3, 0.9)
        cr.set_line_width(8)
        angle = math.pi + (self.value / 100) * math.pi
        cr.arc(cx, cy, r, math.pi, angle)
        cr.stroke()
        # Text
        cr.set_source_rgba(0.9, 0.9, 0.9, 1)
        cr.select_font_face("monospace")
        cr.set_font_size(14)
        text = f"{self.value:.0f}%"
        ext = cr.text_extents(text)
        cr.move_to(cx - ext.width / 2, cy - 5)
        cr.show_text(text)


class TopicWidget(Gtk.Box):
    """Widget for a single MQTT topic."""
    def __init__(self, topic, widget_type="text"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.set_margin_start(8)
        self.set_margin_end(8)
        self.set_margin_top(8)
        self.set_margin_bottom(8)
        self.add_css_class("card")
        self.topic = topic
        self.widget_type = widget_type

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                        margin_start=12, margin_end=12, margin_top=8, margin_bottom=8)
        inner.append(Gtk.Label(label=topic, css_classes=["heading"], xalign=0, ellipsize=True))

        if widget_type == "gauge":
            self.gauge = GaugeWidget()
            inner.append(self.gauge)
            self.value_label = Gtk.Label(label="--", css_classes=["monospace"])
            inner.append(self.value_label)
        elif widget_type == "sparkline":
            self.sparkline = SparklineWidget()
            inner.append(self.sparkline)
            self.value_label = Gtk.Label(label="--", css_classes=["monospace"])
            inner.append(self.value_label)
        else:
            self.value_label = Gtk.Label(label=_("Waiting..."), css_classes=["monospace"],
                                          wrap=True, xalign=0, selectable=True)
            inner.append(self.value_label)

        self.ts_label = Gtk.Label(label="", css_classes=["dim-label", "caption"], xalign=1)
        inner.append(self.ts_label)
        self.append(inner)

    def update(self, payload):
        self.value_label.set_label(str(payload)[:500])
        self.ts_label.set_label(datetime.now().strftime("%H:%M:%S"))
        if self.widget_type == "gauge":
            self.gauge.set_value(payload)
        elif self.widget_type == "sparkline":
            self.sparkline.add_value(payload)


class MqttDashboardWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs, title=_("MQTT Dashboard"), default_width=1000, default_height=700)
        self.client = None
        self.topic_widgets = {}
        self.config = self._load_config()

        header = Adw.HeaderBar()
        self.theme_btn = Gtk.Button(icon_name="weather-clear-night-symbolic")
        self.theme_btn.connect("clicked", self._toggle_theme)
        header.pack_end(self.theme_btn)
        about_btn = Gtk.Button(icon_name="help-about-symbolic")
        about_btn.connect("clicked", self._show_about)
        header.pack_end(about_btn)
        save_btn = Gtk.Button(icon_name="document-save-symbolic", tooltip_text=_("Save layout"))
        save_btn.connect("clicked", self._save_config)
        header.pack_end(save_btn)

        # Connection bar
        conn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
                           margin_start=12, margin_end=12, margin_top=8)
        conn_box.append(Gtk.Label(label=_("Broker:")))
        self.host_entry = Gtk.Entry(text=self.config.get("host", "localhost"), width_chars=20)
        conn_box.append(self.host_entry)
        conn_box.append(Gtk.Label(label=_("Port:")))
        self.port_entry = Gtk.Entry(text=str(self.config.get("port", 1883)), width_chars=6)
        conn_box.append(self.port_entry)

        self.connect_btn = Gtk.Button(label=_("Connect"), css_classes=["suggested-action"])
        self.connect_btn.connect("clicked", self._toggle_connection)
        conn_box.append(self.connect_btn)

        self.conn_status = Gtk.Label(label=_("Disconnected"), css_classes=["dim-label"])
        conn_box.append(self.conn_status)

        # Subscribe bar
        sub_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
                          margin_start=12, margin_end=12, margin_top=4)
        sub_box.append(Gtk.Label(label=_("Subscribe:")))
        self.sub_entry = Gtk.Entry(placeholder_text="topic/# or sensor/temperature", hexpand=True)
        sub_box.append(self.sub_entry)

        type_list = Gtk.StringList.new(["text", "gauge", "sparkline"])
        self.type_combo = Gtk.DropDown(model=type_list)
        sub_box.append(self.type_combo)

        sub_btn = Gtk.Button(label=_("Subscribe"))
        sub_btn.connect("clicked", self._subscribe_topic)
        sub_box.append(sub_btn)

        # Publish bar
        pub_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
                          margin_start=12, margin_end=12, margin_top=4)
        pub_box.append(Gtk.Label(label=_("Publish:")))
        self.pub_topic = Gtk.Entry(placeholder_text="topic", width_chars=20)
        pub_box.append(self.pub_topic)
        self.pub_msg = Gtk.Entry(placeholder_text=_("message"), hexpand=True)
        pub_box.append(self.pub_msg)
        pub_btn = Gtk.Button(label=_("Send"))
        pub_btn.connect("clicked", self._publish)
        pub_box.append(pub_btn)

        # Dashboard area
        sw = Gtk.ScrolledWindow(vexpand=True, margin_start=12, margin_end=12, margin_top=8, margin_bottom=4)
        self.flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,
                                 homogeneous=False, max_children_per_line=4, min_children_per_line=1)
        sw.set_child(self.flow)

        # Messages log
        log_expander = Gtk.Expander(label=_("Message Log"), margin_start=12, margin_end=12)
        sw2 = Gtk.ScrolledWindow(min_content_height=120)
        self.log_view = Gtk.TextView(monospace=True, editable=False, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        sw2.set_child(self.log_view)
        log_expander.set_child(sw2)

        self.statusbar = Gtk.Label(label="", xalign=0, css_classes=["dim-label"], margin_start=12, margin_bottom=4)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(header)
        content.append(conn_box)
        content.append(sub_box)
        content.append(pub_box)
        content.append(sw)
        content.append(log_expander)
        content.append(self.statusbar)
        self.set_content(content)

        # Restore subscriptions
        for sub in self.config.get("subscriptions", []):
            self._add_topic_widget(sub["topic"], sub.get("type", "text"))

        GLib.timeout_add_seconds(1, self._update_status)

    def _toggle_connection(self, _btn):
        if not HAS_MQTT:
            self.conn_status.set_label(_("paho-mqtt not installed!"))
            return

        if self.client:
            self.client.disconnect()
            self.client = None
            self.connect_btn.set_label(_("Connect"))
            self.conn_status.set_label(_("Disconnected"))
            return

        host = self.host_entry.get_text().strip()
        port = int(self.port_entry.get_text().strip())
        self.client = mqtt.Client()
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect

        def connect():
            try:
                self.client.connect(host, port, 60)
                self.client.loop_start()
            except Exception as e:
                GLib.idle_add(self.conn_status.set_label, f"Error: {e}")

        threading.Thread(target=connect, daemon=True).start()
        self.connect_btn.set_label(_("Disconnect"))
        self.conn_status.set_label(_("Connecting..."))

    def _on_connect(self, client, userdata, flags, rc):
        GLib.idle_add(self.conn_status.set_label, _("Connected"))
        # Re-subscribe to existing topics
        for topic in self.topic_widgets:
            client.subscribe(topic)

    def _on_disconnect(self, client, userdata, rc):
        GLib.idle_add(self.conn_status.set_label, _("Disconnected"))

    def _on_message(self, client, userdata, msg):
        topic = msg.topic
        try:
            payload = msg.payload.decode('utf-8')
        except Exception:
            payload = str(msg.payload)
        GLib.idle_add(self._handle_message, topic, payload)

    def _handle_message(self, topic, payload):
        # Update matching widgets (support wildcards by matching all)
        for t, widget in self.topic_widgets.items():
            if topic == t or self._topic_matches(t, topic):
                widget.update(payload)

        # Log
        buf = self.log_view.get_buffer()
        ts = datetime.now().strftime("%H:%M:%S")
        buf.insert(buf.get_end_iter(), f"[{ts}] {topic}: {payload}\n")

    def _topic_matches(self, pattern, topic):
        """Simple MQTT wildcard matching."""
        if pattern == "#":
            return True
        pp = pattern.split("/")
        tp = topic.split("/")
        for i, p in enumerate(pp):
            if p == "#":
                return True
            if i >= len(tp):
                return False
            if p == "+":
                continue
            if p != tp[i]:
                return False
        return len(pp) == len(tp)

    def _subscribe_topic(self, _btn):
        topic = self.sub_entry.get_text().strip()
        if not topic:
            return
        types = ["text", "gauge", "sparkline"]
        wtype = types[self.type_combo.get_selected()]
        self._add_topic_widget(topic, wtype)
        if self.client:
            self.client.subscribe(topic)

    def _add_topic_widget(self, topic, wtype):
        if topic in self.topic_widgets:
            return
        w = TopicWidget(topic, wtype)
        self.topic_widgets[topic] = w
        self.flow.append(w)

    def _publish(self, _btn):
        if not self.client:
            return
        topic = self.pub_topic.get_text().strip()
        msg = self.pub_msg.get_text()
        if topic:
            self.client.publish(topic, msg)

    def _save_config(self, _btn=None):
        self.config = {
            "host": self.host_entry.get_text(),
            "port": int(self.port_entry.get_text()),
            "subscriptions": [
                {"topic": t, "type": w.widget_type}
                for t, w in self.topic_widgets.items()
            ]
        }
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        with open(CONFIG_FILE, 'w') as f:
            json.dump(self.config, f, indent=2)

    def _load_config(self):
        try:
            with open(CONFIG_FILE) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _update_status(self):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        n = len(self.topic_widgets)
        self.statusbar.set_label(f"  {n} subscriptions | {now}")
        return True

    def _toggle_theme(self, _btn):
        mgr = Adw.StyleManager.get_default()
        if mgr.get_dark():
            mgr.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
        else:
            mgr.set_color_scheme(Adw.ColorScheme.FORCE_DARK)

    def _show_about(self, _btn):
        about = Adw.AboutWindow(
            transient_for=self,
            application_name="MQTT Dashboard",
            application_icon="network-server-symbolic",
            version="0.1.0",
            developer_name="Daniel Nylander",
            developers=["Daniel Nylander"],
            license_type=Gtk.License.GPL_3_0,
            website="https://github.com/yeager/mqtt-dashboard",
            issue_url="https://github.com/yeager/mqtt-dashboard/issues",
            translator_credits=_("translator-credits"),
            comments=_("MQTT dashboard with real-time monitoring"),
        )
        about.add_link(_("Translations"), "https://www.transifex.com/danielnylander/mqtt-dashboard")
        about.present(self)


class MqttDashboardApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)

    def do_activate(self):
        win = self.props.active_window or MqttDashboardWindow(application=self)
        win.present()

    def do_startup(self):
        Adw.Application.do_startup(self)
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self.quit())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Control>q"])


def main():
    app = MqttDashboardApp()
    app.run()


if __name__ == "__main__":
    main()
