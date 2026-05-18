from copy import deepcopy
from datetime import datetime
import json
import math
import os
import time
import traceback

import ezdxf
import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.widgets import Button, TextBox
from PIL import Image


# ============================================================
# 基本設定
# ============================================================

IMAGE_FILE = "map.png"

OUTPUT_PREFIX = "output"

SCALE_FILE = "scale.json"

PRINT_PAPER_SIZES_MM = {
    "A4横": (297, 210),
    "A3横": (420, 297),
}

PRINT_MARGIN_MM = 10

# ezdxf の AutoCAD Color Index に近い色を matplotlib 用にも用意します。
# color は DXF 用、plot_color は画面表示用です。
LAYERS = {
    "1": {"name": "ROAD", "label": "道路", "color": 1, "plot_color": "#d62728"},
    "2": {"name": "SITE", "label": "敷地", "color": 3, "plot_color": "#2ca02c"},
    "3": {"name": "SLOPE", "label": "法面", "color": 5, "plot_color": "#1f77b4"},
    "4": {"name": "STRUCTURE", "label": "構造物", "color": 2, "plot_color": "#ffbf00"},
}

BACKGROUND_LAYER = {
    "name": "IMAGE",
    "label": "背景画像",
    "color": 8,
}


def disable_conflicting_matplotlib_shortcuts():
    """CAD用キーと matplotlib 標準ショートカットの競合を減らします。

    matplotlib は標準で s=保存、q=終了、c=戻る等のショートカットを持っています。
    今回はアプリ側でキー操作を管理したいので、よく競合するものを空にします。
    """

    keymap_names = [
        "keymap.fullscreen",
        "keymap.home",
        "keymap.back",
        "keymap.forward",
        "keymap.pan",
        "keymap.zoom",
        "keymap.save",
        "keymap.quit",
        "keymap.grid",
        "keymap.yscale",
        "keymap.xscale",
    ]

    for name in keymap_names:
        mpl.rcParams[name] = []


def configure_matplotlib_fonts():
    """Macで日本語UIが文字化けしにくいようにフォント候補を指定します。"""

    mpl.rcParams["font.family"] = "sans-serif"
    mpl.rcParams["font.sans-serif"] = [
        "Hiragino Sans",
        "Hiragino Maru Gothic Pro",
        "Yu Gothic",
        "Noto Sans CJK JP",
        "Noto Sans JP",
        "IPAexGothic",
        "Meiryo",
        "DejaVu Sans",
    ]
    mpl.rcParams["axes.unicode_minus"] = False


def distance(p1, p2):
    """2点間の距離を返します。縮尺設定で使います。"""

    return math.sqrt((p2[0] - p1[0]) ** 2 + (p2[1] - p1[1]) ** 2)


class SimpleCadApp:
    """Google Earth画像をトレースしてDXF出力する簡易CADアプリです。

    まずは matplotlib ベースを維持し、イベント処理と状態管理を整理します。
    将来ほかのGUIへ移す場合も、このクラスの考え方を移植しやすくなります。
    """

    def __init__(self):
        self.image = Image.open(IMAGE_FILE)

        # 確定済みの線・面です。
        # 例: {"layer": {...}, "points": [(x, y), ...], "closed": False}
        self.lines = []

        # 作図中の点列です。右クリックや n/c キーで確定します。
        self.current_points = []

        # 現在選択中の点です。
        # ("current", point_index) または ("line", line_index, point_index)
        self.selected_point = None

        self.current_layer_key = "1"

        # 縮尺。画像1pxが何mに相当するかを持ちます。
        self.scale_mode = False
        self.scale_points = []
        self.meters_per_pixel = 1.0
        self.scale_dialog_timer = None
        self.scale_pixel_distance = None
        self.scale_input_text = None
        self.scale_input_ax = None
        self.scale_ok_ax = None
        self.scale_input_box = None
        self.scale_ok_button = None
        self.scale_submit_cid = None
        self.scale_ok_cid = None
        self.scale_input_error = ""
        self.notice_message = ""
        self.notice_timer = None
        self.notice_expires_at = None
        self.action_message = ""
        self.action_timer = None
        self.load_scale_settings()

        # Undo/Redo は現在の作図状態をスナップショット保存する方式にします。
        # 初心者向けツールでは、この方式が実装しやすく安全です。
        self.undo_stack = []
        self.redo_stack = []

        # Pan操作用の状態です。
        self.space_down = False
        self.pan_active = False
        self.pan_start = None
        self.pan_start_screen = None
        self.pan_start_xlim = None
        self.pan_start_ylim = None
        self.pan_draw_pending = False
        self.pan_timer = None

        # matplotlibのイベント中に ax.clear() 付きの全面再描画を何度も行うと、
        # GUIバックエンドによってはイベントループが詰まることがあります。
        # そこで redraw() は即時実行せず、短いタイマーで1回にまとめます。
        self.redraw_scheduled = False
        self.pending_reset_view = False
        self.is_redrawing = False
        self.redraw_again_after_current = False
        self.redraw_timer = None

        # 操作状態を明示して、Pan中に作図確定などが割り込まないようにします。
        self.interaction_mode = "idle"
        self.event_cids = {}

        self.fig, self.image_ax = plt.subplots(figsize=(12, 9))
        self.ax = self.image_ax
        self.fig.subplots_adjust(right=0.80, bottom=0.12)

        self.status_text = self.fig.text(
            0.02,
            0.025,
            "",
            fontsize=9,
            color="#222222",
        )

        self.layer_title_text = None
        self.scale_reference_text = None
        self.layer_buttons = {}
        self.ui_axes_set = set()
        self.save_button = None
        self.undo_button = None
        self.redo_button = None
        self.close_button = None
        self.scale_button = None

        self.view_initialized = False

        self.setup_widgets()
        self.connect_events()
        self.print_help()
        self.redraw(reset_view=True, immediate=True)

    # --------------------------------------------------------
    # 基本情報
    # --------------------------------------------------------

    def current_layer(self):
        """現在選択されているレイヤ設定を返します。"""

        return LAYERS[self.current_layer_key]

    def get_status_message(self):
        """画面下に出すステータス文字列を作ります。"""

        mode = self.get_mode_code()
        total_points = len(self.current_points) + sum(
            len(line["points"]) for line in self.lines
        )
        selected = "あり" if self.selected_point is not None else "なし"
        return (
            f"MODE: {mode}  |  "
            f"レイヤ: {self.current_layer()['label']}  |  "
            f"縮尺: 1px = {self.meters_per_pixel:.4f}m  |  "
            f"点数: {total_points}点 "
            f"(作図中 {len(self.current_points)} / 縮尺 {len(self.scale_points)}/2)  |  "
            f"作図中: {len(self.current_points)}点  |  "
            f"選択: {selected}"
        )

    def get_mode_code(self):
        """現場で一目で分かる短いモード名を返します。"""

        if self.interaction_mode == "scaling" or self.scale_mode:
            return "SCALE"

        if self.interaction_mode == "panning" or self.pan_active:
            return "PAN"

        return "DRAW"

    def is_scaling_active(self):
        """縮尺設定のクリック受付中かどうかを返します。"""

        return self.interaction_mode == "scaling" or self.scale_mode

    def get_print_scale_denominator(self, paper_width_mm, paper_height_mm):
        """画像全体を用紙内に収める場合の参考縮尺分母を返します。"""

        printable_width_mm = paper_width_mm - PRINT_MARGIN_MM * 2
        printable_height_mm = paper_height_mm - PRINT_MARGIN_MM * 2

        if printable_width_mm <= 0 or printable_height_mm <= 0:
            return None

        real_width_mm = self.image.width * self.meters_per_pixel * 1000
        real_height_mm = self.image.height * self.meters_per_pixel * 1000

        width_scale = real_width_mm / printable_width_mm
        height_scale = real_height_mm / printable_height_mm

        return max(width_scale, height_scale)

    def get_reference_scale_message(self):
        """右側パネルへ表示する座標換算と参考印刷縮尺を作ります。"""

        lines = [
            f"座標換算:\n1px = {self.meters_per_pixel:.4f}m",
            "参考縮尺:",
        ]

        for label, (paper_width_mm, paper_height_mm) in PRINT_PAPER_SIZES_MM.items():
            denominator = self.get_print_scale_denominator(
                paper_width_mm,
                paper_height_mm,
            )

            if denominator is None:
                scale_text = "計算不可"
            else:
                scale_text = f"約1/{round(denominator):,}"

            lines.append(f"{label} 全体印刷:\n{scale_text}")

        return "\n".join(lines)

    def load_scale_settings(self):
        """前回保存した縮尺設定を scale.json から読み込みます。

        Google Earthの距離測定で一度合わせた縮尺を、次回起動時も使えるようにします。
        ファイルが無い場合や壊れている場合は、初期値 1px = 1m のまま続行します。
        """

        if not os.path.exists(SCALE_FILE):
            return

        try:
            with open(SCALE_FILE, "r", encoding="utf-8") as file:
                data = json.load(file)
        except (OSError, json.JSONDecodeError):
            print(f"{SCALE_FILE} を読み込めませんでした。初期縮尺を使います")
            return

        loaded_scale = data.get("meters_per_pixel")

        if not isinstance(loaded_scale, (int, float)) or loaded_scale <= 0:
            print(f"{SCALE_FILE} の縮尺値が不正です。初期縮尺を使います")
            return

        self.meters_per_pixel = float(loaded_scale)
        print(f"前回の縮尺を読み込みました: 1px = {self.meters_per_pixel:.4f}m")

    def save_scale_settings(self):
        """現在の縮尺設定を scale.json に保存します。"""

        data = {
            "meters_per_pixel": self.meters_per_pixel,
            "image_file": IMAGE_FILE,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }

        try:
            with open(SCALE_FILE, "w", encoding="utf-8") as file:
                json.dump(data, file, ensure_ascii=False, indent=2)
        except OSError as error:
            print(f"{SCALE_FILE} を保存できませんでした: {error}")
            return

        print(f"縮尺設定を保存しました: {SCALE_FILE}")

    def get_toolbar_mode(self):
        """matplotlibツールバーの現在モードを文字列で返します。"""

        manager = getattr(self.fig.canvas, "manager", None)
        toolbar = getattr(manager, "toolbar", None)

        if toolbar is None:
            return ""

        return str(getattr(toolbar, "mode", ""))

    def deactivate_toolbar_mode(self):
        """ツールバーのPan/Zoom状態を解除します。

        ツールバーがPan/Zoomを掴んだままだと、アプリ側のクリック処理が
        期待通りに動かないことがあります。縮尺設定を始める前に解除します。
        """

        manager = getattr(self.fig.canvas, "manager", None)
        toolbar = getattr(manager, "toolbar", None)

        if toolbar is None:
            return

        mode_text = self.get_toolbar_mode().lower()

        try:
            if "pan" in mode_text:
                toolbar.pan()
                print("ツールバーのPanモードを解除しました")
            elif "zoom" in mode_text:
                toolbar.zoom()
                print("ツールバーのZoomモードを解除しました")
        except Exception as error:
            # ツールバー実装はバックエンドで少し差があります。
            # 解除に失敗してもアプリ自体は続行します。
            print(f"ツールバー状態の解除に失敗しました: {error}")

    def debug_click_event(self, event, reason=""):
        """クリック処理の状態をターミナルへ出します。

        縮尺設定中に点が打てない時、mode / toolbar / xdata / ydata を見ると
        どこで無視されたかを追いやすくなります。
        """

        print(
            "[click-debug] "
            f"reason={reason} | "
            f"interaction_mode={self.interaction_mode} | "
            f"current_layer={self.current_layer()['label']} | "
            f"scale_mode={self.scale_mode} | "
            f"scale_points={len(self.scale_points)} | "
            f"current_points={len(self.current_points)} | "
            f"is_ui_axes={self.is_ui_axes(getattr(event, 'inaxes', None))} | "
            f"success_message_active={self.is_notice_active()} | "
            f"toolbar.mode={self.get_toolbar_mode()} | "
            f"button={getattr(event, 'button', None)} | "
            f"event.inaxes={getattr(event, 'inaxes', None)} | "
            f"inaxes_is_image_ax={getattr(event, 'inaxes', None) == self.image_ax} | "
            f"event.inaxes_repr={repr(getattr(event, 'inaxes', None))} | "
            f"image_ax_repr={repr(self.image_ax)} | "
            f"xdata={getattr(event, 'xdata', None)} | "
            f"ydata={getattr(event, 'ydata', None)}"
        )

    def debug_app_state(self, reason):
        """イベント無しの状態確認ログを出します。"""

        print(
            f"[state-debug] reason={reason} | "
            f"interaction_mode={self.interaction_mode} | "
            f"current_layer={self.current_layer()['label']} | "
            f"scale_mode={self.scale_mode} | "
            f"scale_points={len(self.scale_points)} | "
            f"current_points={len(self.current_points)} | "
            f"success_message_active={self.is_notice_active()} | "
            f"button_press_cid={self.event_cids.get('button_press_event')} | "
            f"image_ax_in_ui_axes={self.image_ax in self.ui_axes_set} | "
            f"redraw_scheduled={self.redraw_scheduled} | "
            f"is_redrawing={self.is_redrawing}"
        )

    # --------------------------------------------------------
    # 画面UI
    # --------------------------------------------------------

    def register_ui_axes(self, ax):
        """右側パネルや入力欄など、画像作図ではないaxesを登録します。"""

        self.ui_axes_set.add(ax)
        return ax

    def unregister_ui_axes(self, ax):
        """削除済みUI axesをクリック判定対象から外します。"""

        if ax is not None:
            self.ui_axes_set.discard(ax)

    def is_ui_axes(self, ax):
        """指定されたaxesが右側UI用かどうかを返します。"""

        return ax in self.ui_axes_set

    def is_ui_click(self, event):
        """実際に右側UI領域をクリックしたイベントかどうかを返します。"""

        event_ax = getattr(event, "inaxes", None)
        if not self.is_ui_axes(event_ax):
            return False

        if getattr(event, "x", None) is None or getattr(event, "y", None) is None:
            return True

        return event_ax.bbox.contains(event.x, event.y)

    def setup_widgets(self):
        """右側にレイヤ切り替えと主要操作ボタンを置きます。"""

        self.layer_title_text = self.fig.text(
            0.835,
            0.93,
            "",
            fontsize=12,
            fontweight="bold",
            color="#222222",
        )
        self.scale_reference_text = self.fig.text(
            0.835,
            0.66,
            "",
            fontsize=9,
            color="#222222",
            va="top",
        )

        for index, (key, layer) in enumerate(LAYERS.items()):
            layer_ax = self.register_ui_axes(
                self.fig.add_axes([0.84, 0.84 - index * 0.055, 0.14, 0.045])
            )
            button = Button(layer_ax, layer["label"])
            button.on_clicked(
                self.safe_callback(
                    f"change_layer:{layer['label']}",
                    lambda event, layer_key=key: self.change_layer(layer_key),
                )
            )
            self.layer_buttons[key] = button

        save_ax = self.register_ui_axes(self.fig.add_axes([0.84, 0.46, 0.14, 0.045]))
        undo_ax = self.register_ui_axes(self.fig.add_axes([0.84, 0.40, 0.065, 0.045]))
        redo_ax = self.register_ui_axes(self.fig.add_axes([0.915, 0.40, 0.065, 0.045]))
        close_ax = self.register_ui_axes(self.fig.add_axes([0.84, 0.34, 0.14, 0.045]))
        scale_ax = self.register_ui_axes(self.fig.add_axes([0.84, 0.28, 0.14, 0.045]))

        self.save_button = Button(save_ax, "DXF保存")
        self.undo_button = Button(undo_ax, "Undo")
        self.redo_button = Button(redo_ax, "Redo")
        self.close_button = Button(close_ax, "閉じて確定")
        self.scale_button = Button(scale_ax, "縮尺設定")

        self.save_button.on_clicked(
            self.safe_callback("save_dxf", lambda event: self.save_dxf())
        )
        self.undo_button.on_clicked(
            self.safe_callback("undo", lambda event: self.undo())
        )
        self.redo_button.on_clicked(
            self.safe_callback("redo", lambda event: self.redo())
        )
        self.close_button.on_clicked(
            self.safe_callback(
                "finish_current_line_closed",
                lambda event: self.finish_current_line(closed=True),
            )
        )
        self.scale_button.on_clicked(
            self.safe_callback("start_scale_mode", lambda event: self.start_scale_mode())
        )
        self.update_layer_ui()

    def safe_callback(self, name, callback):
        """matplotlib Button callback の例外を必ずターミナルへ出します。"""

        def wrapped(event):
            try:
                return callback(event)
            except Exception:
                print(f"[callback-error] {name}")
                traceback.print_exc()
                return None

        return wrapped

    def update_layer_ui(self):
        """右側パネルで現在レイヤが一目で分かるように表示します。"""

        if self.layer_title_text is not None:
            self.layer_title_text.set_text(
                f"現在レイヤ:\n{self.current_layer()['label']}"
            )

        if self.scale_reference_text is not None:
            self.scale_reference_text.set_text(self.get_reference_scale_message())

        for key, button in self.layer_buttons.items():
            is_current = key == self.current_layer_key
            button.ax.set_facecolor("#ffe8e8" if is_current else "#f2f2f2")
            button.color = "#ffe8e8" if is_current else "#f2f2f2"
            button.hovercolor = "#ffd6d6" if is_current else "#e6e6e6"
            button.label.set_fontweight("bold" if is_current else "normal")
            button.label.set_color("#b00020" if is_current else "#222222")

    def get_image_event_point(self, event):
        """画像axes上のクリック座標を返します。

        一部バックエンドで event.inaxes が None になる場合でも、画面座標が
        image_ax の表示範囲内なら data座標へ復元して扱います。
        """

        if self.is_ui_click(event):
            return None

        if getattr(event, "x", None) is None or getattr(event, "y", None) is None:
            if getattr(event, "inaxes", None) == self.image_ax:
                if event.xdata is None or event.ydata is None:
                    return None
                return event.xdata, event.ydata
            return None

        # 通常作図の安定性を優先し、event.inaxes より画面座標の画像bbox判定を優先します。
        if self.image_ax.bbox.contains(event.x, event.y):
            xdata, ydata = self.image_ax.transData.inverted().transform((event.x, event.y))
            return xdata, ydata

        if getattr(event, "inaxes", None) != self.image_ax:
            return None

        if event.xdata is None or event.ydata is None:
            return None

        return event.xdata, event.ydata

    def connect_events(self):
        """matplotlib のイベントとアプリの処理を接続します。"""

        canvas = self.fig.canvas
        self.event_cids = {
            "button_press_event": canvas.mpl_connect(
                "button_press_event", self.on_mouse_press
            ),
            "button_release_event": canvas.mpl_connect(
                "button_release_event", self.on_mouse_release
            ),
            "motion_notify_event": canvas.mpl_connect(
                "motion_notify_event", self.on_mouse_move
            ),
            "scroll_event": canvas.mpl_connect("scroll_event", self.on_scroll),
            "key_press_event": canvas.mpl_connect("key_press_event", self.on_key_press),
            "key_release_event": canvas.mpl_connect(
                "key_release_event", self.on_key_release
            ),
            "close_event": canvas.mpl_connect("close_event", self.on_figure_close),
        }
        print(f"[event-connected] {self.event_cids}")

    def on_figure_close(self, event):
        """figure が閉じられた瞬間に理由追跡用ログを出します。"""

        print(
            "[figure-close] "
            f"event={event} | "
            f"interaction_mode={self.interaction_mode} | "
            f"current_layer={self.current_layer()['label']} | "
            f"current_points={len(self.current_points)} | "
            f"manager_exists={self.fig.canvas.manager is not None} | "
            f"fignum_exists={plt.fignum_exists(self.fig.number)}"
        )

    def redraw(self, reset_view=False, immediate=False):
        """画面再描画を要求します。

        通常はすぐに描かず、少し後のタイマーにまとめます。
        Undo連打や右クリック確定のように短時間に複数イベントが来ても、
        ax.clear() を連続実行しにくくするためです。
        """

        self.pending_reset_view = self.pending_reset_view or reset_view

        if immediate:
            self.perform_redraw()
            return

        if self.redraw_scheduled:
            return

        self.redraw_scheduled = True

        # new_timer はバックエンドのイベントループに処理を戻してから呼ばれるため、
        # イベントハンドラ内で重い描画を直接走らせるより安定します。
        self.redraw_timer = self.fig.canvas.new_timer(interval=15)
        self.redraw_timer.single_shot = True
        self.redraw_timer.add_callback(self.perform_redraw)
        self.redraw_timer.start()

    def perform_redraw(self):
        """画面全体を描き直します。

        ax.clear() するとズーム位置も初期化されるため、通常は現在の表示範囲を
        保存してから描き直します。
        """

        if self.is_redrawing:
            self.redraw_again_after_current = True
            return False

        self.expire_temporary_notice_if_needed(redraw=False)

        self.is_redrawing = True
        self.redraw_scheduled = False
        reset_view = self.pending_reset_view
        self.pending_reset_view = False

        if self.view_initialized and not reset_view:
            xlim = self.ax.get_xlim()
            ylim = self.ax.get_ylim()
        else:
            xlim = None
            ylim = None

        self.ax.clear()
        self.ax.imshow(self.image)
        self.ax.set_aspect("equal", adjustable="box")

        self.ax.set_title(
            "左クリック=点追加/点選択  右クリック=線確定  Space+ドラッグ=移動  "
            "ホイール=ズーム  Esc=キャンセル  Delete=点削除",
            fontsize=10,
        )

        self.draw_confirmed_lines()
        self.draw_current_line()
        self.draw_scale_ui()

        if xlim is None or ylim is None:
            self.ax.set_xlim(0, self.image.width)
            self.ax.set_ylim(self.image.height, 0)
            self.view_initialized = True
        else:
            self.ax.set_xlim(xlim)
            self.ax.set_ylim(ylim)

        self.draw_overlay()
        self.draw_status_bar()
        self.update_layer_ui()
        self.fig.canvas.draw_idle()

        self.is_redrawing = False
        print(
            "[after-redraw] "
            f"interaction_mode={self.interaction_mode} | "
            f"current_layer={self.current_layer()['label']} | "
            f"current_points={len(self.current_points)} | "
            f"button_press_cid={self.event_cids.get('button_press_event')} | "
            f"image_ax_in_ui_axes={self.image_ax in self.ui_axes_set} | "
            f"redraw_scheduled={self.redraw_scheduled} | "
            f"is_redrawing={self.is_redrawing}"
        )

        if self.redraw_again_after_current:
            self.redraw_again_after_current = False
            self.redraw()

        # TimerBaseのコールバックは False を返すと繰り返し登録されにくく、
        # バックエンド差があっても単発タイマーとして扱いやすくなります。
        return False

    def request_fast_canvas_draw(self):
        """Pan/Zoom用の軽い再描画要求です。

        ax.clear() はせず、表示範囲だけを変えたあと draw_idle() します。
        motion_notify_event は大量に来るので、同じイベントループ内では1回に寄せます。
        """

        if self.pan_draw_pending:
            return

        self.pan_draw_pending = True
        self.fig.canvas.draw_idle()

        self.pan_timer = self.fig.canvas.new_timer(interval=10)
        self.pan_timer.single_shot = True
        self.pan_timer.add_callback(self.clear_pan_draw_pending)
        self.pan_timer.start()

    def clear_pan_draw_pending(self):
        """Pan/Zoomの軽量描画フラグを戻します。"""

        self.pan_draw_pending = False
        self.pan_timer = None
        return False

    def show_temporary_notice(self, message, duration_ms=3000):
        """操作完了などの一時メッセージを画面上に表示します。"""

        self.notice_message = message
        self.notice_expires_at = time.monotonic() + duration_ms / 1000

        if self.notice_timer is not None:
            try:
                self.notice_timer.stop()
            except Exception:
                pass

        self.notice_timer = self.fig.canvas.new_timer(interval=duration_ms)
        self.notice_timer.single_shot = True
        self.notice_timer.add_callback(self.clear_temporary_notice)
        self.notice_timer.start()
        self.redraw()

    def clear_temporary_notice(self):
        """一時メッセージを消します。"""

        self.notice_message = ""
        self.notice_timer = None
        self.notice_expires_at = None
        self.redraw()
        return False

    def is_notice_active(self):
        """成功メッセージが現在表示中かどうかを返します。"""

        self.expire_temporary_notice_if_needed(redraw=False)
        return bool(self.notice_message)

    def expire_temporary_notice_if_needed(self, redraw=True):
        """タイマーが動かない環境でも、期限切れ成功メッセージを消します。"""

        if self.notice_expires_at is None:
            return

        if time.monotonic() < self.notice_expires_at:
            return

        self.notice_message = ""
        self.notice_expires_at = None
        self.notice_timer = None

        if redraw:
            self.redraw()

    def show_action_message(self, message, duration_ms=1200):
        """点追加などの短い操作フィードバックを表示します。"""

        self.action_message = message

        if self.action_timer is not None:
            try:
                self.action_timer.stop()
            except Exception:
                pass

        self.action_timer = self.fig.canvas.new_timer(interval=duration_ms)
        self.action_timer.single_shot = True
        self.action_timer.add_callback(self.clear_action_message)
        self.action_timer.start()

    def clear_action_message(self):
        """短い操作フィードバックを消します。"""

        self.action_message = ""
        self.action_timer = None
        self.redraw()
        return False

    def stop_scale_dialog_timer(self):
        """予約中の縮尺入力UI表示があれば止めます。"""

        if self.scale_dialog_timer is None:
            return

        try:
            self.scale_dialog_timer.stop()
        except Exception:
            pass

        self.scale_dialog_timer = None

    def cleanup_scale_ui(self):
        """縮尺設定用のmatplotlib UIとイベント接続を完全に片付けます。"""

        self.stop_scale_dialog_timer()

        if self.scale_input_box is not None and self.scale_submit_cid is not None:
            try:
                self.scale_input_box.disconnect(self.scale_submit_cid)
            except Exception:
                pass

        if self.scale_ok_button is not None and self.scale_ok_cid is not None:
            try:
                self.scale_ok_button.disconnect(self.scale_ok_cid)
            except Exception:
                pass

        for widget in (self.scale_input_box, self.scale_ok_button):
            if widget is not None:
                try:
                    widget.disconnect_events()
                except Exception:
                    pass

        self.release_mouse_grab()

        for attr_name in ("scale_input_ax", "scale_ok_ax"):
            ax = getattr(self, attr_name)
            if ax is not None:
                self.unregister_ui_axes(ax)
                try:
                    ax.remove()
                except ValueError:
                    pass
                setattr(self, attr_name, None)

        if self.scale_input_text is not None:
            try:
                self.scale_input_text.remove()
            except ValueError:
                pass

        self.scale_input_text = None
        self.scale_input_box = None
        self.scale_ok_button = None
        self.scale_submit_cid = None
        self.scale_ok_cid = None
        self.scale_pixel_distance = None
        self.scale_input_error = ""

    def clear_scale_input_ui(self):
        """古い呼び名からも縮尺UIの片付けを使えるようにします。"""

        self.cleanup_scale_ui()

    def release_mouse_grab(self):
        """UI widgetがcanvasのmouse grabを掴んだままにしないよう解除します。"""

        canvas = self.fig.canvas
        for ax in list(self.ui_axes_set) + [self.image_ax]:
            try:
                canvas.release_mouse(ax)
            except Exception:
                pass

    def draw_confirmed_lines(self):
        """確定済みの線・面を描画します。"""

        for line_index, line in enumerate(self.lines):
            pts = line["points"]
            if not pts:
                continue

            color = line["layer"].get("plot_color", "#333333")
            is_closed = line.get("closed", False)

            draw_pts = pts + [pts[0]] if is_closed and len(pts) >= 3 else pts

            if len(draw_pts) >= 2:
                xs = [p[0] for p in draw_pts]
                ys = [p[1] for p in draw_pts]
                self.ax.plot(xs, ys, "-", color=color, linewidth=2)

                if is_closed:
                    self.ax.fill(xs, ys, color=color, alpha=0.12)

            for point_index, (x, y) in enumerate(pts):
                marker = "o"
                marker_size = 4
                marker_color = color

                if self.selected_point == ("line", line_index, point_index):
                    marker = "s"
                    marker_size = 8
                    marker_color = "#000000"

                self.ax.plot(x, y, marker, color=marker_color, markersize=marker_size)

    def draw_current_line(self):
        """作図中の線を描画します。"""

        if not self.current_points:
            return

        color = "#d62728"
        xs = [p[0] for p in self.current_points]
        ys = [p[1] for p in self.current_points]

        if len(self.current_points) >= 2:
            self.ax.plot(
                xs,
                ys,
                "-",
                color=color,
                linewidth=3.5,
                zorder=12,
            )

        for point_index, (x, y) in enumerate(self.current_points):
            marker = "o"
            marker_size = 10
            marker_color = color

            if self.selected_point == ("current", point_index):
                marker = "s"
                marker_size = 11
                marker_color = "#000000"

            self.ax.plot(
                x,
                y,
                marker,
                color=marker_color,
                markeredgecolor="#ffffff",
                markeredgewidth=2,
                markersize=marker_size,
                zorder=13,
            )

    def draw_overlay(self):
        """現在モードや縮尺操作の案内を画面左上へ大きく表示します。"""

        mode = self.get_mode_code()
        self.ax.text(
            0.015,
            0.975,
            f"MODE: {mode}",
            transform=self.ax.transAxes,
            ha="left",
            va="top",
            fontsize=13,
            fontweight="bold",
            color="#ffffff",
            bbox={
                "boxstyle": "round,pad=0.35",
                "facecolor": "#222222",
                "edgecolor": "#ffffff",
                "alpha": 0.72,
            },
            picker=False,
            zorder=20,
        )

        if self.is_scaling_active():
            if len(self.scale_points) == 0:
                title = "縮尺設定中"
                message = "距離が分かる2点をクリックしてください"
            elif len(self.scale_points) == 1:
                title = "1点目を取得しました"
                message = "2点目をクリックしてください"
            else:
                title = "2点を取得しました"
                message = "右側に実距離(m)を入力してください"

            self.ax.text(
                0.015,
                0.905,
                f"{title}\n{message}\nEscでキャンセル",
                transform=self.ax.transAxes,
                ha="left",
                va="top",
                fontsize=18,
                fontweight="bold",
                linespacing=1.45,
                color="#ffffff",
                bbox={
                    "boxstyle": "round,pad=0.55",
                    "facecolor": "#0057d9",
                    "edgecolor": "#ffffff",
                    "linewidth": 1.5,
                    "alpha": 0.78,
                },
                picker=False,
                zorder=21,
            )

        if self.notice_message:
            self.ax.text(
                0.5,
                0.93,
                self.notice_message,
                transform=self.ax.transAxes,
                ha="center",
                va="top",
                fontsize=17,
                fontweight="bold",
                linespacing=1.35,
                color="#ffffff",
                bbox={
                    "boxstyle": "round,pad=0.5",
                    "facecolor": "#167a3a",
                    "edgecolor": "#ffffff",
                    "linewidth": 1.5,
                    "alpha": 0.86,
                },
                picker=False,
                zorder=22,
            )

        if self.action_message:
            self.ax.text(
                0.015,
                0.82,
                self.action_message,
                transform=self.ax.transAxes,
                ha="left",
                va="top",
                fontsize=13,
                fontweight="bold",
                color="#ffffff",
                bbox={
                    "boxstyle": "round,pad=0.35",
                    "facecolor": "#d62728",
                    "edgecolor": "#ffffff",
                    "linewidth": 1.2,
                    "alpha": 0.86,
                },
                picker=False,
                zorder=23,
            )

    def draw_status_bar(self):
        """画面下のステータスバーを更新します。"""

        self.status_text.set_text(self.get_status_message())

    def draw_scale_ui(self):
        """縮尺設定中の基準点と基準線を青色で強調表示します。"""

        if not self.scale_points:
            return

        for point_index, (x, y) in enumerate(self.scale_points, start=1):
            self.ax.plot(
                x,
                y,
                "o",
                color="#006bff",
                markeredgecolor="#ffffff",
                markeredgewidth=2,
                markersize=13,
                zorder=15,
            )
            self.ax.text(
                x,
                y,
                str(point_index),
                ha="center",
                va="center",
                fontsize=8,
                fontweight="bold",
                color="#ffffff",
                zorder=16,
            )

        if len(self.scale_points) == 2:
            xs = [p[0] for p in self.scale_points]
            ys = [p[1] for p in self.scale_points]
            self.ax.plot(
                xs,
                ys,
                color="#006bff",
                linestyle="--",
                linewidth=3,
                zorder=14,
            )

    # --------------------------------------------------------
    # Undo / Redo
    # --------------------------------------------------------

    def make_snapshot(self):
        """Undo/Redo 用に現在状態を保存します。"""

        return {
            "lines": deepcopy(self.lines),
            "current_points": deepcopy(self.current_points),
            "current_layer_key": self.current_layer_key,
            "scale_mode": self.scale_mode,
            "scale_points": deepcopy(self.scale_points),
            "meters_per_pixel": self.meters_per_pixel,
            "selected_point": deepcopy(self.selected_point),
        }

    def restore_snapshot(self, snapshot):
        """保存した状態に戻します。"""

        self.lines = deepcopy(snapshot["lines"])
        self.current_points = deepcopy(snapshot["current_points"])
        self.current_layer_key = snapshot["current_layer_key"]
        self.scale_mode = snapshot["scale_mode"]
        self.scale_points = deepcopy(snapshot["scale_points"])
        self.meters_per_pixel = snapshot["meters_per_pixel"]
        self.selected_point = deepcopy(snapshot["selected_point"])

    def push_undo(self):
        """変更前の状態をUndo履歴へ積みます。"""

        self.undo_stack.append(self.make_snapshot())
        self.redo_stack.clear()

    def undo(self):
        """1つ前の状態に戻します。"""

        if self.is_scaling_active():
            print("縮尺設定中のため、Undoは一時停止しています")
            return

        if not self.undo_stack:
            print("Undoできる操作がありません")
            return

        self.redo_stack.append(self.make_snapshot())
        self.restore_snapshot(self.undo_stack.pop())
        print("Undoしました")
        self.redraw()

    def redo(self):
        """Undoした操作をやり直します。"""

        if self.is_scaling_active():
            print("縮尺設定中のため、Redoは一時停止しています")
            return

        if not self.redo_stack:
            print("Redoできる操作がありません")
            return

        self.undo_stack.append(self.make_snapshot())
        self.restore_snapshot(self.redo_stack.pop())
        print("Redoしました")
        self.redraw()

    # --------------------------------------------------------
    # マウス操作
    # --------------------------------------------------------

    def on_mouse_press(self, event):
        """クリック開始時の処理です。"""

        is_scaling = self.interaction_mode == "scaling" or self.scale_mode

        self.debug_click_event(event, reason="mouse_press")

        if self.is_ui_click(event):
            self.debug_click_event(event, reason="ignored:ui_axes_click")
            return

        if self.is_redrawing and not is_scaling:
            self.debug_click_event(event, reason="continued:during_redraw")

        image_point = self.get_image_event_point(event)

        if image_point is None:
            if is_scaling:
                self.debug_click_event(event, reason="ignored:not_image_axes")
            return

        self.debug_click_event(event, reason="image-click")

        # 縮尺設定中は、他の作図操作より優先して左クリック2回を拾います。
        # ここを先に処理することで、点選択や通常作図に吸われる事故を防ぎます。
        if is_scaling:
            if event.button != 1:
                self.debug_click_event(event, reason="ignored:scale_requires_left_click")
                return

            self.add_scale_point(image_point)
            return

        # Space + 左ドラッグ、または中ボタンドラッグでPanします。
        if (event.button == 1 and self.space_down) or event.button == 2:
            self.start_pan(event, image_point)
            return

        # 右クリックはCAD風に「現在の線を確定」にします。
        if event.button == 3:
            if self.interaction_mode == "panning":
                return
            self.finish_current_line(closed=False)
            return

        # 左クリック以外は無視します。
        if event.button != 1:
            return

        point = image_point

        # 作図中は連続トレースを最優先します。
        # 近くの既存点選択に吸われると2点目以降を打てないため、
        # current_points がある間は左クリックを必ず点追加として扱います。
        if self.current_points or self.interaction_mode == "drawing":
            self.add_current_point(point)
            return

        # 既存点の近くをクリックした場合は、点追加ではなく選択にします。
        nearest = self.find_nearest_point(event)
        if nearest is not None:
            self.selected_point = nearest
            print("点を選択しました。Deleteで削除できます")
            self.redraw()
            return

        self.add_current_point(point)

    def add_current_point(self, point):
        """通常作図の点を追加し、作図モードを維持します。"""

        self.push_undo()
        self.current_points.append(point)
        self.selected_point = ("current", len(self.current_points) - 1)
        self.scale_mode = False
        self.scale_points = []
        self.interaction_mode = "drawing"
        print(
            "[draw-point-added] "
            f"interaction_mode={self.interaction_mode} | "
            f"scale_mode={self.scale_mode} | "
            f"current_points={len(self.current_points)} | "
            f"point=({point[0]:.2f}, {point[1]:.2f})"
        )
        self.show_action_message(f"点を追加しました: {len(self.current_points)}点")
        self.redraw(immediate=True)
        self.fig.canvas.draw_idle()

    def on_mouse_release(self, event):
        """ドラッグ終了時の処理です。"""

        self.pan_active = False
        self.pan_start = None
        self.pan_start_screen = None
        self.pan_start_xlim = None
        self.pan_start_ylim = None
        if self.interaction_mode == "panning":
            self.interaction_mode = "idle"

    def on_mouse_move(self, event):
        """マウス移動時の処理です。今はPanだけ扱います。"""

        if not self.pan_active:
            return

        if event.x is None or event.y is None:
            return

        # Pan中に xlim/ylim を変えると event.xdata/ydata も変化します。
        # その値を基準にするとドラッグ量が揺れるため、画面ピクセル差分から
        # data座標差分へ変換します。
        dx_px = event.x - self.pan_start_screen[0]
        dy_px = event.y - self.pan_start_screen[1]

        bbox = self.ax.bbox
        x_units_per_px = (self.pan_start_xlim[1] - self.pan_start_xlim[0]) / bbox.width
        y_units_per_px = (self.pan_start_ylim[1] - self.pan_start_ylim[0]) / bbox.height

        dx = dx_px * x_units_per_px
        dy = dy_px * y_units_per_px

        self.ax.set_xlim(
            self.pan_start_xlim[0] - dx,
            self.pan_start_xlim[1] - dx,
        )
        self.ax.set_ylim(
            self.pan_start_ylim[0] - dy,
            self.pan_start_ylim[1] - dy,
        )

        self.request_fast_canvas_draw()

    def on_scroll(self, event):
        """マウスホイールで、カーソル位置を中心にズームします。"""

        if event.inaxes != self.ax:
            return

        if event.xdata is None or event.ydata is None:
            return

        zoom_factor = 0.80 if event.button == "up" else 1.25

        xlim = self.ax.get_xlim()
        ylim = self.ax.get_ylim()

        x_range = (xlim[1] - xlim[0]) * zoom_factor
        y_range = (ylim[1] - ylim[0]) * zoom_factor

        x_center = event.xdata
        y_center = event.ydata

        x_ratio = (x_center - xlim[0]) / (xlim[1] - xlim[0])
        y_ratio = (y_center - ylim[0]) / (ylim[1] - ylim[0])

        self.ax.set_xlim(
            x_center - x_range * x_ratio,
            x_center + x_range * (1 - x_ratio),
        )
        self.ax.set_ylim(
            y_center - y_range * y_ratio,
            y_center + y_range * (1 - y_ratio),
        )

        self.request_fast_canvas_draw()

    def start_pan(self, event, point=None):
        """Pan操作を開始します。"""

        self.pan_active = True
        self.pan_start = point if point is not None else (event.xdata, event.ydata)
        self.pan_start_screen = (event.x, event.y)
        self.pan_start_xlim = self.ax.get_xlim()
        self.pan_start_ylim = self.ax.get_ylim()
        self.interaction_mode = "panning"

    # --------------------------------------------------------
    # キーボード操作
    # --------------------------------------------------------

    def on_key_press(self, event):
        """キーボード入力を処理します。"""

        key = event.key

        if key == " ":
            if self.is_scaling_active():
                return
            self.space_down = True
            return

        if key == "escape":
            self.cancel_current_line()
            return

        if self.is_scaling_active():
            if self.scale_input_box is None:
                print("縮尺設定中です。2点をクリックするか、Escでキャンセルしてください")
            return

        if key in ("ctrl+z", "cmd+z"):
            self.undo()
            return

        if key in ("ctrl+y", "cmd+shift+z"):
            self.redo()
            return

        if key in ("delete", "backspace"):
            self.delete_selected_point()
            return

        if key == "k":
            self.start_scale_mode()
            return

        if key == "u":
            self.undo()
            return

        if key == "r":
            self.redo()
            return

        if key == "n":
            self.finish_current_line(closed=False)
            return

        if key == "c":
            self.finish_current_line(closed=True)
            return

        if key == "d":
            self.delete_selected_or_last_line()
            return

        if key in LAYERS:
            self.change_layer(key)
            return

        if key == "enter":
            self.save_dxf()
            return

        if key == "q":
            print("終了します")
            plt.close(self.fig)

    def on_key_release(self, event):
        """キーを離した時の処理です。Space Panを終了します。"""

        if event.key == " ":
            self.space_down = False

    # --------------------------------------------------------
    # 作図操作
    # --------------------------------------------------------

    def finish_current_line(self, closed=False):
        """作図中の点列を、線または閉じた面として確定します。"""

        if self.is_scaling_active():
            print("縮尺設定中のため、作図確定は一時停止しています")
            return

        min_points = 3 if closed else 2

        if len(self.current_points) < min_points:
            print(f"確定には点が{min_points}つ以上必要です")
            return

        self.push_undo()

        self.lines.append(
            {
                "layer": self.current_layer().copy(),
                "points": self.current_points.copy(),
                "closed": closed,
            }
        )

        print(
            f"確定: {self.current_layer()['label']} / "
            f"{'閉じた面' if closed else '線'}"
        )

        self.current_points = []
        self.selected_point = None
        self.interaction_mode = "idle"
        self.redraw()

    def cancel_current_line(self):
        """Escで作図中の線、または縮尺設定をキャンセルします。"""

        if self.is_scaling_active():
            self.clear_scale_input_ui()
            self.scale_mode = False
            self.scale_points = []
            self.interaction_mode = "idle"
            print("縮尺設定をキャンセルしました")
            self.redraw()
            return

        if not self.current_points:
            self.selected_point = None
            self.interaction_mode = "idle"
            self.redraw()
            return

        self.push_undo()
        self.current_points = []
        self.selected_point = None
        self.interaction_mode = "idle"
        print("作図中の線をキャンセルしました")
        self.redraw()

    def delete_selected_or_last_line(self):
        """選択点がなければ、最後に確定した線を削除します。"""

        if self.is_scaling_active():
            print("縮尺設定中のため、削除は一時停止しています")
            return

        if self.selected_point is not None:
            self.delete_selected_point()
            return

        if not self.lines:
            print("削除できる線がありません")
            return

        self.push_undo()
        removed = self.lines.pop()
        print(f"最後の線を削除しました: {removed['layer']['label']}")
        self.redraw()

    def change_layer(self, layer_key):
        """現在レイヤを変更します。"""

        if self.is_scaling_active():
            print("縮尺設定中のため、レイヤ変更は一時停止しています")
            return

        self.deactivate_toolbar_mode()
        self.release_mouse_grab()
        self.current_layer_key = layer_key
        self.interaction_mode = "drawing"
        self.scale_mode = False
        self.scale_points = []
        self.selected_point = None
        self.pan_active = False
        self.space_down = False
        print(f"レイヤ変更: {self.current_layer()['label']}")
        self.debug_app_state("layer-change")
        self.update_layer_ui()
        self.draw_status_bar()
        self.fig.canvas.draw_idle()
        self.release_mouse_grab()
        print(
            "[layer-change-complete] "
            f"interaction_mode={self.interaction_mode} | "
            f"current_layer={self.current_layer()['label']} | "
            f"scale_mode={self.scale_mode} | "
            f"current_points={len(self.current_points)} | "
            f"button_press_cid={self.event_cids.get('button_press_event')} | "
            f"image_ax_in_ui_axes={self.image_ax in self.ui_axes_set} | "
            f"manager_exists={self.fig.canvas.manager is not None} | "
            f"fignum_exists={plt.fignum_exists(self.fig.number)}"
        )

    def on_layer_radio_clicked(self, label):
        """右側のRadioButtonsからレイヤを切り替えます。"""

        for key, layer in LAYERS.items():
            if layer["label"] == label:
                self.change_layer(key)
                break

    # --------------------------------------------------------
    # 点選択・削除
    # --------------------------------------------------------

    def find_nearest_point(self, event, threshold_px=10):
        """クリック位置から近い点を探します。

        data座標ではなく画面ピクセル距離で判定するため、
        ズーム倍率に左右されにくい選択になります。
        """

        candidates = []

        for index, point in enumerate(self.current_points):
            candidates.append((("current", index), point))

        for line_index, line in enumerate(self.lines):
            for point_index, point in enumerate(line["points"]):
                candidates.append((("line", line_index, point_index), point))

        if not candidates:
            return None

        click_xy = (event.x, event.y)
        nearest_ref = None
        nearest_distance = None

        for ref, point in candidates:
            screen_xy = self.ax.transData.transform(point)
            px_distance = distance(click_xy, screen_xy)

            if nearest_distance is None or px_distance < nearest_distance:
                nearest_distance = px_distance
                nearest_ref = ref

        if nearest_distance is not None and nearest_distance <= threshold_px:
            return nearest_ref

        return None

    def delete_selected_point(self):
        """選択中の点を削除します。"""

        if self.is_scaling_active():
            print("縮尺設定中のため、点削除は一時停止しています")
            return

        if self.selected_point is None:
            print("削除する点が選択されていません")
            return

        self.push_undo()

        if self.selected_point[0] == "current":
            point_index = self.selected_point[1]
            if 0 <= point_index < len(self.current_points):
                self.current_points.pop(point_index)
                print("作図中の点を削除しました")

        elif self.selected_point[0] == "line":
            line_index = self.selected_point[1]
            point_index = self.selected_point[2]

            if 0 <= line_index < len(self.lines):
                line = self.lines[line_index]

                if 0 <= point_index < len(line["points"]):
                    line["points"].pop(point_index)
                    print("確定済み図形の点を削除しました")

                # 線は2点未満、閉じた面は3点未満になると図形として成立しません。
                min_points = 3 if line.get("closed", False) else 2
                if len(line["points"]) < min_points:
                    self.lines.pop(line_index)
                    print("点数不足のため図形を削除しました")

        self.selected_point = None
        self.redraw()

    # --------------------------------------------------------
    # 縮尺設定
    # --------------------------------------------------------

    def start_scale_mode(self):
        """縮尺設定モードを開始します。"""

        self.deactivate_toolbar_mode()
        self.clear_scale_input_ui()

        self.pan_active = False
        self.pan_start = None
        self.pan_start_screen = None
        self.pan_start_xlim = None
        self.pan_start_ylim = None
        self.space_down = False
        self.scale_mode = True
        self.scale_points = []
        self.selected_point = None
        self.notice_message = ""
        self.notice_expires_at = None
        self.interaction_mode = "scaling"

        print("")
        print("=== 縮尺設定モード ===")
        print("距離が分かる2点をクリックしてください")
        print(f"現在の interaction_mode: {self.interaction_mode}")
        print(f"現在の toolbar.mode: {self.get_toolbar_mode()}")

        self.redraw()

    def add_scale_point(self, point):
        """縮尺設定用の点を追加します。2点揃ったら実距離を入力します。"""

        self.scale_points.append(point)
        print(
            f"縮尺設定点を追加しました: "
            f"{len(self.scale_points)}/2 "
            f"({point[0]:.2f}, {point[1]:.2f})"
        )
        self.redraw()

        if len(self.scale_points) != 2:
            return

        pixel_distance = distance(self.scale_points[0], self.scale_points[1])

        print("")
        print(f"画像上距離: {pixel_distance:.2f}px")

        if pixel_distance <= 0:
            print("2点間距離が0pxです。縮尺設定をキャンセルします")
            self.clear_scale_input_ui()
            self.scale_mode = False
            self.scale_points = []
            self.interaction_mode = "idle"
            self.redraw()
            return

        self.schedule_scale_distance_input(pixel_distance)

    def schedule_scale_distance_input(self, pixel_distance):
        """2点クリック後、イベント処理を一度抜けてから距離入力欄を表示します。

        クリックイベントの中で直接ウィジェットを作るより、短いタイマーを挟むと
        2点目の表示を反映してから入力待ちに入れるため見た目が安定します。
        """

        if self.scale_dialog_timer is not None:
            return

        self.scale_dialog_timer = self.fig.canvas.new_timer(interval=80)
        self.scale_dialog_timer.single_shot = True
        self.scale_dialog_timer.add_callback(
            lambda: self.show_scale_distance_input(pixel_distance)
        )
        self.scale_dialog_timer.start()

    def show_scale_distance_input(self, pixel_distance):
        """matplotlib画面内に実距離入力欄とOKボタンを表示します。"""

        self.scale_dialog_timer = None

        if not self.is_scaling_active() or len(self.scale_points) != 2:
            return False

        self.clear_scale_input_ui()
        self.scale_pixel_distance = pixel_distance

        self.scale_input_text = self.fig.text(
            0.84,
            0.245,
            f"実距離(m)を入力してください\n画像上距離: {pixel_distance:.2f}px",
            fontsize=9,
            color="#222222",
            va="top",
        )
        self.scale_input_ax = self.register_ui_axes(
            self.fig.add_axes([0.84, 0.175, 0.14, 0.04])
        )
        self.scale_ok_ax = self.register_ui_axes(
            self.fig.add_axes([0.84, 0.12, 0.14, 0.04])
        )

        self.scale_input_box = TextBox(
            self.scale_input_ax,
            "",
            initial="",
            color="#ffffff",
            hovercolor="#eef5ff",
        )
        self.scale_ok_button = Button(self.scale_ok_ax, "OK")
        self.scale_ok_cid = self.scale_ok_button.on_clicked(
            lambda event: self.apply_scale_input()
        )
        self.scale_submit_cid = self.scale_input_box.on_submit(
            lambda text: self.apply_scale_input()
        )

        self.scale_input_box.set_val("")
        self.redraw()
        return False

    def apply_scale_input(self):
        """入力された実距離から縮尺を計算して保存します。"""

        if self.scale_input_box is None or self.scale_pixel_distance is None:
            return

        text = self.scale_input_box.text.strip()

        try:
            real_distance = float(text)
        except ValueError:
            self.scale_input_error = "数値を入力してください"
            print("実距離は数値で入力してください")
            self.update_scale_input_prompt()
            return

        if real_distance <= 0:
            self.scale_input_error = "0より大きい距離を入力してください"
            print("実距離は0より大きい値を入力してください")
            self.update_scale_input_prompt()
            return

        pixel_distance = self.scale_pixel_distance

        self.push_undo()
        self.meters_per_pixel = real_distance / pixel_distance
        self.save_scale_settings()

        print("")
        print(f"縮尺設定完了: 1px = {self.meters_per_pixel:.4f}m")

        self.cleanup_scale_ui()
        self.scale_mode = False
        self.scale_points = []
        self.selected_point = None
        self.pan_active = False
        self.space_down = False
        self.interaction_mode = "drawing"
        self.debug_scale_complete_state()
        self.show_temporary_notice(
            f"縮尺設定完了:\n1px = {self.meters_per_pixel:.4f}m"
        )

    def debug_scale_complete_state(self):
        """縮尺完了後に通常作図へ戻れているかをログへ出します。"""

        print("")
        print("[scale-complete]")
        print(f"interaction_mode={self.interaction_mode}")
        print(f"scale_mode={self.scale_mode}")
        print(f"scale_points={len(self.scale_points)}")
        print(f"scale_input_box={self.scale_input_box}")
        print(f"scale_ok_button={self.scale_ok_button}")
        print(f"current_layer={self.current_layer()['label']}")
        print(f"success_message_active={self.is_notice_active()}")
        print(f"redraw_scheduled={self.redraw_scheduled}")
        print(f"is_redrawing={self.is_redrawing}")

    def update_scale_input_prompt(self):
        """縮尺入力欄の案内文を更新します。"""

        if self.scale_input_text is None or self.scale_pixel_distance is None:
            return

        message = (
            "実距離(m)を入力してください\n"
            f"画像上距離: {self.scale_pixel_distance:.2f}px"
        )

        if self.scale_input_error:
            message += f"\n{self.scale_input_error}"

        self.scale_input_text.set_text(message)
        self.fig.canvas.draw_idle()

    # --------------------------------------------------------
    # DXF出力
    # --------------------------------------------------------

    def image_point_to_cad_point(self, x, y):
        """画像座標をCAD座標に変換します。

        matplotlib画像座標は左上が原点で、下方向にyが増えます。
        CAD座標は一般的に左下を原点として上方向にyが増えるため、
        y座標を反転してから縮尺を掛けます。
        """

        cad_x = x * self.meters_per_pixel
        cad_y = (self.image.height - y) * self.meters_per_pixel

        return cad_x, cad_y

    def add_layers_to_dxf(self, doc):
        """DXF内に必要なレイヤを作成します。"""

        for layer in LAYERS.values():
            if layer["name"] not in doc.layers:
                doc.layers.add(name=layer["name"], color=layer["color"])

        if BACKGROUND_LAYER["name"] not in doc.layers:
            doc.layers.add(
                name=BACKGROUND_LAYER["name"],
                color=BACKGROUND_LAYER["color"],
            )

    def add_background_image_to_dxf(self, doc, msp):
        """DXFへ背景画像参照を追加します。

        注意:
        DXFに画像ファイルそのものを埋め込むのではなく、map.png への参照を保存します。
        AutoCAD / TREND-CORE 側で表示するには、DXFと map.png を同じフォルダに置く運用が安全です。
        """

        if not os.path.exists(IMAGE_FILE):
            print("背景画像が見つからないため、DXF画像参照は追加しません")
            return

        image_def = doc.add_image_def(
            filename=IMAGE_FILE,
            size_in_pixel=(self.image.width, self.image.height),
        )

        msp.add_image(
            image_def,
            insert=(0, 0),
            size_in_units=(
                self.image.width * self.meters_per_pixel,
                self.image.height * self.meters_per_pixel,
            ),
            dxfattribs={"layer": BACKGROUND_LAYER["name"]},
        )

    def add_lines_to_dxf(self, msp):
        """確定済みの線・面をDXFに追加します。"""

        for line in self.lines:
            layer_name = line["layer"]["name"]

            cad_points = [
                self.image_point_to_cad_point(x, y)
                for x, y in line["points"]
            ]

            if len(cad_points) < 2:
                continue

            msp.add_lwpolyline(
                cad_points,
                close=line.get("closed", False),
                dxfattribs={"layer": layer_name},
            )

    def save_dxf(self):
        """現在の図形をDXFとして保存します。"""

        if len(self.current_points) >= 2:
            self.finish_current_line(closed=False)

        doc = ezdxf.new("R2010")
        doc.units = ezdxf.units.M

        msp = doc.modelspace()
        self.add_layers_to_dxf(doc)
        self.add_background_image_to_dxf(doc, msp)
        self.add_lines_to_dxf(msp)

        filename = f"{OUTPUT_PREFIX}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.dxf"
        doc.saveas(filename)

        print("")
        print(f"DXF保存完了: {filename}")
        print(f"保存時縮尺: 1px = {self.meters_per_pixel:.4f}m")
        print("背景画像は外部参照です。DXFと map.png を同じフォルダで管理してください")

    # --------------------------------------------------------
    # ヘルプ
    # --------------------------------------------------------

    def print_help(self):
        """起動時にターミナルへ操作方法を表示します。"""

        print("")
        print("=== 操作方法 ===")
        print("左クリック: 点を追加 / 既存点を選択")
        print("右クリック: 作図中の線を確定")
        print("Space + 左ドラッグ: 表示移動(Pan)")
        print("中ボタンドラッグ: 表示移動(Pan)")
        print("マウスホイール: ズーム")
        print("Esc: 作図中の線または縮尺設定をキャンセル")
        print("Delete / Backspace: 選択点を削除")
        print("u / Cmd+Z / Ctrl+Z: Undo")
        print("r / Cmd+Shift+Z / Ctrl+Y: Redo")
        print("n: 線として確定")
        print("c: 閉じた面として確定")
        print("d: 選択点を削除。選択がなければ最後の線を削除")
        print("k: 縮尺設定")
        print("1: 道路 / 2: 敷地 / 3: 法面 / 4: 構造物")
        print("Enter: DXF保存")
        print("q: 終了")
        print("================")


def main():
    configure_matplotlib_fonts()
    disable_conflicting_matplotlib_shortcuts()
    app = SimpleCadApp()
    try:
        print(
            "[mainloop-start] "
            f"manager_exists={app.fig.canvas.manager is not None} | "
            f"fignum_exists={plt.fignum_exists(app.fig.number)}"
        )
        plt.show()
    finally:
        print(
            "[mainloop-ended] "
            f"manager_exists={app.fig.canvas.manager is not None} | "
            f"fignum_exists={plt.fignum_exists(app.fig.number)} | "
            f"interaction_mode={app.interaction_mode} | "
            f"current_layer={app.current_layer()['label']} | "
            f"current_points={len(app.current_points)}"
        )


if __name__ == "__main__":
    main()
