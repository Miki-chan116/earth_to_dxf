from copy import deepcopy
from datetime import datetime
import json
import math
import os
import time
import traceback

import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.backend_bases import cursors
from matplotlib.widgets import Button, TextBox
from PIL import Image

from src.constants import (
    A3_FOCUS_SCALE,
    CIVIL_STANDARD_SCALES,
    CONFIRMED_LINE_WIDTH,
    CONFIRMED_MARKER_SIZE,
    CROSSHAIR_CENTER_SIZE,
    CROSSHAIR_DOT_SIZE,
    CROSSHAIR_LINE_WIDTH,
    CROSSHAIR_MODE,
    CROSSHAIR_MODES,
    CROSSHAIR_SMALL_OFFSET_PX,
    CROSSHAIR_UPDATE_INTERVAL_MS,
    CURRENT_LINE_WIDTH,
    CURRENT_MARKER_SIZE,
    DEBUG,
    DEFAULT_IMAGE_FILE,
    LAYERS,
    MARKER_EDGE_WIDTH,
    PRINT_MARGIN_MM,
    PRINT_PAPER_SIZES_MM,
    PROJECT_FILE,
    SCALE_FILE,
    SCALE_LINE_WIDTH,
    SCALE_MARKER_SIZE,
    SELECTED_MARKER_SIZE,
)
from src.config_store import (
    get_background_image_path,
    get_storable_image_path,
    normalize_image_path,
    save_config as save_config_file,
)
from src.project_store import (
    build_project_data as build_project_file_data,
    deserialize_lines as deserialize_project_lines,
    deserialize_points as deserialize_project_points,
    get_existing_project_created_at,
    get_project_background_warning,
    load_project as load_project_file,
    save_project as save_project_file,
)
from src.dxf_export import export_dxf
from src.gsi_tile import (
    fetch_gsi_tile_grid,
    get_tile_type_label,
    load_gsi_settings,
    save_gsi_settings,
    validate_gsi_settings,
)
from src.scale_utils import (
    image_fits_paper_at_scale as calc_image_fits_paper_at_scale,
    nearest_standard_scale,
    next_fitting_standard_scale,
    print_scale_denominator,
    printed_image_size_mm,
    standard_scale_pair,
)


# ============================================================
# 基本設定
# ============================================================


def debug_log(*args, **kwargs):
    """DEBUG=True のときだけ詳細ログを出します。"""

    if DEBUG:
        print(*args, **kwargs)


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
        self.background_image_path = self.load_config()
        self.image = Image.open(self.background_image_path)
        self.save_config()

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
        self.scale_needs_reset = False
        self.scale_reset_message = ""
        self.notice_message = ""
        self.notice_timer = None
        self.notice_expires_at = None
        self.action_message = ""
        self.action_timer = None
        self.info_detail_expanded = False
        self.gsi_settings = load_gsi_settings()
        self.gsi_settings_visible = False
        self.gsi_settings_texts = []
        self.gsi_settings_axes = []
        self.gsi_settings_boxes = {}
        self.gsi_settings_ok_button = None
        self.gsi_settings_cancel_button = None
        self.gsi_settings_error_text = None
        self.gsi_settings_button = None
        self.crosshair_mode = (
            CROSSHAIR_MODE if CROSSHAIR_MODE in CROSSHAIR_MODES else "small"
        )
        self.crosshair_enabled = self.crosshair_mode != "off"
        self.mouse_image_point = None
        self.crosshair_image_point = None
        self.crosshair_horizontal = None
        self.crosshair_vertical = None
        self.crosshair_center = None
        self.crosshair_dot = None
        self.crosshair_last_draw_at = 0.0
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
        self.operation_title_text = None
        self.info_title_text = None
        self.scale_reference_text = None
        self.layer_buttons = {}
        self.ui_axes_set = set()
        self.save_button = None
        self.undo_button = None
        self.redo_button = None
        self.close_button = None
        self.scale_button = None
        self.gsi_settings_button = None
        self.gsi_tile_button = None
        self.project_save_button = None
        self.project_load_button = None
        self.info_detail_button = None

        self.view_initialized = False

        self.setup_widgets()
        self.connect_events()
        self.print_help()
        self.load_project(auto=True, show_notice=False, redraw=False)
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
        mouse_position = self.get_mouse_position_message()
        return (
            f"MODE: {mode}  |  "
            f"レイヤ: {self.current_layer()['label']}  |  "
            f"縮尺: 1px = {self.meters_per_pixel:.4f}m  |  "
            f"{mouse_position}  |  "
            f"点数: {total_points}点 "
            f"(作図中 {len(self.current_points)} / 縮尺 {len(self.scale_points)}/2)  |  "
            f"作図中: {len(self.current_points)}点  |  "
            f"選択: {selected}  |  "
            f"十字: {'ON' if self.crosshair_enabled else 'OFF'}({self.crosshair_mode})"
        )

    def get_mouse_position_message(self):
        """ステータスバー用に現在マウス位置を画像pxとCAD mで返します。"""

        point = self.get_active_drawing_point()

        if point is None:
            return "クロス中心: -"

        x, y = point
        cad_x = x * self.meters_per_pixel
        cad_y = (self.image.height - y) * self.meters_per_pixel

        return (
            f"クロス中心で作図: x={x:.1f}px y={y:.1f}px / "
            f"X={cad_x:.2f}m Y={cad_y:.2f}m"
        )

    def get_active_drawing_point(self):
        """作図に使う現在位置を返します。"""

        if self.crosshair_enabled and self.crosshair_image_point is not None:
            return self.crosshair_image_point

        return self.mouse_image_point

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

        return print_scale_denominator(
            self.image.width,
            self.image.height,
            self.meters_per_pixel,
            paper_width_mm,
            paper_height_mm,
            PRINT_MARGIN_MM,
        )

    def get_printed_image_size_mm(self, scale_denominator):
        """指定縮尺で画像全体を印刷した場合の用紙上サイズを返します。"""

        return printed_image_size_mm(
            self.image.width,
            self.image.height,
            self.meters_per_pixel,
            scale_denominator,
        )

    def image_fits_paper_at_scale(self, paper_width_mm, paper_height_mm, scale_denominator):
        """指定した用紙・縮尺で画像全体が印刷範囲に収まるかを返します。"""

        return calc_image_fits_paper_at_scale(
            self.image.width,
            self.image.height,
            self.meters_per_pixel,
            paper_width_mm,
            paper_height_mm,
            PRINT_MARGIN_MM,
            scale_denominator,
        )

    def get_nearest_standard_scale(self, denominator):
        """参考縮尺分母に一番近い土木標準縮尺分母を返します。"""

        return nearest_standard_scale(denominator, CIVIL_STANDARD_SCALES)

    def get_standard_scale_pair(self, denominator):
        """参考縮尺を挟む標準縮尺の候補を返します。"""

        return standard_scale_pair(denominator, CIVIL_STANDARD_SCALES)

    def format_standard_scale_recommendation(self, paper_label, denominator, compact=False):
        """右側パネルへ出す標準縮尺の推奨文を作ります。"""

        if denominator is None:
            return f"{paper_label}: 計算不可"

        nearest = self.get_nearest_standard_scale(denominator)
        smaller, larger = self.get_standard_scale_pair(denominator)

        if smaller is not None and larger is not None and smaller != larger:
            if compact:
                return f"{paper_label} 1/{nearest}"

            return (
                f"{paper_label}なら 1/{smaller} または 1/{larger}\n"
                f"最寄り: 1/{nearest}"
            )

        if nearest is not None:
            return f"{paper_label} 1/{nearest}" if compact else f"{paper_label}なら 1/{nearest}"

        return f"{paper_label}: 計算不可"

    def get_next_fitting_standard_scale(self, paper_width_mm, paper_height_mm):
        """画像全体が収まる最小の土木標準縮尺分母を返します。"""

        return next_fitting_standard_scale(
            self.image.width,
            self.image.height,
            self.meters_per_pixel,
            paper_width_mm,
            paper_height_mm,
            PRINT_MARGIN_MM,
            CIVIL_STANDARD_SCALES,
        )

    def get_reference_scale_message(self, detailed=False):
        """右側パネルへ表示する座標換算と参考印刷縮尺を作ります。"""

        background_filename = self.get_panel_filename(self.background_image_path)
        paper_denominators = {}
        lines = [
            f"背景: {background_filename}",
            f"地図: {get_tile_type_label(self.gsi_settings['tile_type'])}",
            f"換算: 1px={self.meters_per_pixel:.4f}m",
        ]

        if self.scale_needs_reset:
            lines.append("縮尺再設定が必要")

        reference_lines = []
        for label, (paper_width_mm, paper_height_mm) in PRINT_PAPER_SIZES_MM.items():
            denominator = self.get_print_scale_denominator(
                paper_width_mm,
                paper_height_mm,
            )
            paper_denominators[label] = denominator
            compact_label = label.replace("横", "")

            if denominator is None:
                scale_text = "計算不可"
            else:
                scale_text = f"1/{round(denominator):,}"

            reference_lines.append(f"{compact_label} {scale_text}")

        lines.append("参考: " + " / ".join(reference_lines))

        a3_recommendation = self.format_standard_scale_recommendation(
            "A3",
            paper_denominators.get("A3横"),
            compact=not detailed,
        )
        lines.append("推奨: " + a3_recommendation)

        if detailed:
            lines.append("標準: 1/250, 1/500, 1/1000, 1/2500, 1/5000")
            lines.append(
                "A4: "
                + self.format_standard_scale_recommendation(
                    "A4",
                    paper_denominators.get("A4横"),
                    compact=True,
                )
            )

            a3_width_mm, a3_height_mm = PRINT_PAPER_SIZES_MM["A3横"]
            fits_a3_500 = self.image_fits_paper_at_scale(
                a3_width_mm,
                a3_height_mm,
                A3_FOCUS_SCALE,
            )

            if fits_a3_500:
                lines.append("A3 1/500: 収まる")
            else:
                next_scale = self.get_next_fitting_standard_scale(
                    a3_width_mm,
                    a3_height_mm,
                )
                if next_scale is None:
                    lines.append("A3 1/500: 不可")
                else:
                    lines.append(f"A3 1/500: 不可 -> 1/{next_scale}")

            lines.append("※印刷・図面作成の目安")

        return "\n".join(lines)

    def get_panel_filename(self, image_path, max_chars=20):
        """右側パネル向けに、背景画像をファイル名だけで短く表示します。"""

        filename = os.path.basename(os.path.normpath(image_path))
        if len(filename) <= max_chars:
            return filename

        keep_head = max_chars - 11
        return f"{filename[:keep_head]}...{filename[-8:]}"

    def normalize_image_path(self, image_path):
        """config.json の画像パスをアプリ起点の絶対パスへそろえます。"""

        return normalize_image_path(image_path)

    def get_image_identity(self, image_path):
        """縮尺の使い回し判定に使う画像名を返します。"""

        return os.path.basename(os.path.normpath(image_path))

    def get_storable_image_path(self, image_path):
        """プロジェクト内の画像は config.json へ相対パスで保存します。"""

        return get_storable_image_path(image_path)

    def load_config(self):
        """背景画像パスを config.json から読み込みます。

        config.json が無い場合は従来どおり map.png を使います。
        """

        return get_background_image_path(default=DEFAULT_IMAGE_FILE)

    def save_config(self):
        """現在の背景画像パスを config.json に保存します。"""

        config = {
            "background_image": self.get_storable_image_path(self.background_image_path),
        }

        if save_config_file(config):
            debug_log("背景画像設定を保存しました: config.json")

    def fetch_default_gsi_tile(self):
        """保存済みの設定を使って地理院タイルを取得します。"""

        if self.scale_mode:
            self.cancel_current_line()

        if self.gsi_settings_visible:
            if not self.apply_gsi_settings(close_on_success=False):
                return

        lat = self.gsi_settings["latitude"]
        lon = self.gsi_settings["longitude"]
        zoom = self.gsi_settings["zoom"]
        grid_size = self.gsi_settings["grid_size"]
        tile_type = self.gsi_settings["tile_type"]

        print("")
        print(
            "地理院タイル取得開始: "
            f"lat={lat}, lon={lon}, zoom={zoom}, "
            f"grid={grid_size}x{grid_size}, map={tile_type}"
        )

        result = fetch_gsi_tile_grid(
            lat,
            lon,
            zoom,
            tile_type=tile_type,
            grid_size=grid_size,
        )

        previous_image = self.image
        self.background_image_path = self.normalize_image_path(result["image_file"])
        self.image = Image.open(self.background_image_path)
        try:
            previous_image.close()
        except Exception:
            pass

        self.meters_per_pixel = result["meters_per_pixel"]
        self.scale_needs_reset = False
        self.scale_reset_message = ""
        self.view_initialized = False

        print(f"地理院タイル取得完了: {result['image_file']}")
        print(
            "center tile: "
            f"z={result['zoom']} "
            f"x={result['center_tile_x']} "
            f"y={result['center_tile_y']} "
            f"grid={result['grid_size']}x{result['grid_size']}"
        )
        print(f"縮尺を自動設定しました: 1px = {self.meters_per_pixel:.4f}m")

        self.show_temporary_notice(
            f"地理院タイル{grid_size}x{grid_size}を取得しました\n"
            f"1px = {self.meters_per_pixel:.4f}m"
        )
        self.redraw(reset_view=True)

    def toggle_gsi_settings_ui(self):
        """地理院タイル取得設定の入力欄を開閉します。"""

        if self.gsi_settings_visible:
            self.cleanup_gsi_settings_ui()
        else:
            self.show_gsi_settings_ui()

    def show_gsi_settings_ui(self):
        """matplotlib画面内に地理院タイル取得設定フォームを表示します。"""

        self.cleanup_gsi_settings_ui()
        self.gsi_settings_visible = True

        panel_x = 0.11
        label_x = 0.12
        input_x = 0.22
        start_y = 0.82
        row_gap = 0.055
        input_width = 0.16
        input_height = 0.035

        title = self.fig.text(
            panel_x,
            start_y + 0.055,
            "地理院タイル取得設定",
            fontsize=12,
            fontweight="bold",
            color="#222222",
            bbox={
                "boxstyle": "round,pad=0.35",
                "facecolor": "#ffffff",
                "edgecolor": "#666666",
                "alpha": 0.94,
            },
            zorder=30,
        )
        self.gsi_settings_texts.append(title)

        fields = [
            ("latitude", "緯度", self.gsi_settings["latitude"]),
            ("longitude", "経度", self.gsi_settings["longitude"]),
            ("zoom", "ズーム", self.gsi_settings["zoom"]),
            ("grid_size", "グリッド", self.gsi_settings["grid_size"]),
            ("tile_type", "地図種別", self.gsi_settings["tile_type"]),
        ]

        for index, (key, label, value) in enumerate(fields):
            y = start_y - index * row_gap
            label_text = self.fig.text(
                label_x,
                y + 0.008,
                label,
                fontsize=9,
                color="#222222",
                zorder=30,
            )
            self.gsi_settings_texts.append(label_text)

            ax = self.register_ui_axes(
                self.fig.add_axes([input_x, y, input_width, input_height])
            )
            textbox = TextBox(
                ax,
                "",
                initial=str(value),
                color="#ffffff",
                hovercolor="#eef5ff",
            )
            self.gsi_settings_axes.append(ax)
            self.gsi_settings_boxes[key] = textbox

        help_text = self.fig.text(
            label_x,
            start_y - 5 * row_gap + 0.015,
            "地図種別: std / pale / seamlessphoto\nグリッド: 1 / 3 / 5",
            fontsize=8,
            color="#333333",
            zorder=30,
        )
        self.gsi_settings_texts.append(help_text)

        self.gsi_settings_error_text = self.fig.text(
            label_x,
            start_y - 5 * row_gap - 0.025,
            "",
            fontsize=8,
            color="#b00020",
            zorder=30,
        )

        ok_button = self.create_panel_button(
            "設定OK",
            start_y - 5 * row_gap - 0.075,
            self.safe_callback("apply_gsi_settings", lambda event: self.apply_gsi_settings()),
            x=label_x,
            width=0.12,
            height=0.035,
        )
        cancel_button = self.create_panel_button(
            "閉じる",
            start_y - 5 * row_gap - 0.075,
            self.safe_callback("close_gsi_settings", lambda event: self.cleanup_gsi_settings_ui()),
            x=label_x + 0.135,
            width=0.12,
            height=0.035,
        )
        self.gsi_settings_ok_button = ok_button
        self.gsi_settings_cancel_button = cancel_button
        self.fig.canvas.draw_idle()

    def cleanup_gsi_settings_ui(self):
        """地理院タイル取得設定フォームを閉じます。"""

        for textbox in self.gsi_settings_boxes.values():
            try:
                textbox.disconnect_events()
            except Exception:
                pass

        for ax in self.gsi_settings_axes:
            self.unregister_ui_axes(ax)
            try:
                ax.remove()
            except ValueError:
                pass

        for button in (self.gsi_settings_ok_button, self.gsi_settings_cancel_button):
            if button is not None:
                self.unregister_ui_axes(button.ax)
                try:
                    button.disconnect_events()
                except Exception:
                    pass
                try:
                    button.ax.remove()
                except ValueError:
                    pass

        for text in self.gsi_settings_texts:
            try:
                text.remove()
            except ValueError:
                pass

        if self.gsi_settings_error_text is not None:
            try:
                self.gsi_settings_error_text.remove()
            except ValueError:
                pass

        self.gsi_settings_texts = []
        self.gsi_settings_axes = []
        self.gsi_settings_boxes = {}
        self.gsi_settings_ok_button = None
        self.gsi_settings_cancel_button = None
        self.gsi_settings_error_text = None
        self.gsi_settings_visible = False
        self.fig.canvas.draw_idle()

    def apply_gsi_settings(self, close_on_success=True):
        """入力された地理院タイル取得設定を検証して保存します。"""

        try:
            settings = validate_gsi_settings(
                self.gsi_settings_boxes["latitude"].text,
                self.gsi_settings_boxes["longitude"].text,
                self.gsi_settings_boxes["zoom"].text,
                self.gsi_settings_boxes["grid_size"].text,
                self.gsi_settings_boxes["tile_type"].text,
            )
            self.gsi_settings = save_gsi_settings(settings)
        except (KeyError, OSError, ValueError) as error:
            message = str(error)
            print(f"地理院設定エラー: {message}")
            if self.gsi_settings_error_text is not None:
                self.gsi_settings_error_text.set_text(message)
            self.fig.canvas.draw_idle()
            return False

        print(
            "地理院設定を保存しました: "
            f"lat={self.gsi_settings['latitude']}, "
            f"lon={self.gsi_settings['longitude']}, "
            f"zoom={self.gsi_settings['zoom']}, "
            f"grid={self.gsi_settings['grid_size']}, "
            f"map={self.gsi_settings['tile_type']}"
        )

        if close_on_success:
            self.cleanup_gsi_settings_ui()
        elif self.gsi_settings_error_text is not None:
            self.gsi_settings_error_text.set_text("")
            self.fig.canvas.draw_idle()

        return True

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
        scale_image_file = data.get("image_file")

        if isinstance(scale_image_file, str) and scale_image_file.strip():
            previous_image_path = self.normalize_image_path(scale_image_file)
            current_image_path = self.normalize_image_path(self.background_image_path)

            if previous_image_path != current_image_path:
                self.scale_needs_reset = True
                self.scale_reset_message = (
                    "背景画像が前回縮尺設定時と違うため、縮尺再設定が必要です"
                )
                previous_image_name = self.get_image_identity(previous_image_path)
                current_image_name = self.get_image_identity(current_image_path)
                print(
                    f"{self.scale_reset_message}: "
                    f"{previous_image_name} -> {current_image_name}"
                )
                return
        elif self.get_image_identity(self.background_image_path) != DEFAULT_IMAGE_FILE:
            self.scale_needs_reset = True
            self.scale_reset_message = (
                "縮尺設定に背景画像名が無いため、縮尺再設定が必要です"
            )
            print(self.scale_reset_message)
            return

        if not isinstance(loaded_scale, (int, float)) or loaded_scale <= 0:
            print(f"{SCALE_FILE} の縮尺値が不正です。初期縮尺を使います")
            return

        self.meters_per_pixel = float(loaded_scale)
        self.scale_needs_reset = False
        self.scale_reset_message = ""
        print(f"前回の縮尺を読み込みました: 1px = {self.meters_per_pixel:.4f}m")

    def save_scale_settings(self):
        """現在の縮尺設定を scale.json に保存します。"""

        data = {
            "meters_per_pixel": self.meters_per_pixel,
            "image_file": self.get_storable_image_path(self.background_image_path),
            "image_filename": self.get_image_identity(self.background_image_path),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }

        try:
            with open(SCALE_FILE, "w", encoding="utf-8") as file:
                json.dump(data, file, ensure_ascii=False, indent=2)
        except OSError as error:
            print(f"{SCALE_FILE} を保存できませんでした: {error}")
            return

        print(f"縮尺設定を保存しました: {SCALE_FILE}")

    def build_project_data(self):
        """現在の作業状態を project.json 用の辞書へまとめます。"""

        return build_project_file_data(
            background_image=self.background_image_path,
            meters_per_pixel=self.meters_per_pixel,
            current_layer=self.current_layer_key,
            lines=self.lines,
            current_points=self.current_points,
            layers=LAYERS,
            created_at=get_existing_project_created_at(PROJECT_FILE),
        )

    def save_project(self):
        """作業途中の状態を project.json に保存します。"""

        data = self.build_project_data()

        success, error_message = save_project_file(PROJECT_FILE, data)
        if not success:
            print(error_message)
            self.show_temporary_notice("作業を保存できませんでした")
            return

        print(f"作業を保存しました: {PROJECT_FILE}")
        self.show_temporary_notice("作業を保存しました")

    def load_project(self, auto=False, show_notice=True, redraw=True):
        """project.json から作業途中の状態を読み込みます。"""

        data, error_message = load_project_file(PROJECT_FILE)
        if error_message:
            if not auto:
                print(error_message)
                if show_notice:
                    if "ありません" in error_message:
                        self.show_temporary_notice("project.json がありません")
                    else:
                        self.show_temporary_notice("作業を読み込めませんでした")
            return False

        warning_message = get_project_background_warning(data, self.background_image_path)
        if warning_message:
            print(warning_message)

        meters_per_pixel = data.get("meters_per_pixel")
        if isinstance(meters_per_pixel, (int, float)) and meters_per_pixel > 0:
            self.meters_per_pixel = float(meters_per_pixel)

        current_layer = data.get("current_layer")
        if isinstance(current_layer, str) and current_layer in LAYERS:
            self.current_layer_key = current_layer

        self.lines = deserialize_project_lines(
            data.get("lines", []),
            layers=LAYERS,
            default_layer_key=self.current_layer_key,
        )
        self.current_points = deserialize_project_points(data.get("current_points", []))
        self.selected_point = None
        self.scale_mode = False
        self.scale_points = []
        self.interaction_mode = "drawing" if self.current_points else "idle"
        self.undo_stack.clear()
        self.redo_stack.clear()

        print(f"作業を読み込みました: {PROJECT_FILE}")

        if show_notice:
            message = "作業を読み込みました"
            if warning_message:
                message += "\n背景画像が違います"
            self.show_temporary_notice(message)

        if redraw:
            self.redraw(reset_view=True)
        else:
            self.update_layer_ui()

        return True

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
                debug_log("ツールバーのPanモードを解除しました")
            elif "zoom" in mode_text:
                toolbar.zoom()
                debug_log("ツールバーのZoomモードを解除しました")
        except Exception as error:
            # ツールバー実装はバックエンドで少し差があります。
            # 解除に失敗してもアプリ自体は続行します。
            debug_log(f"ツールバー状態の解除に失敗しました: {error}")

    def debug_click_event(self, event, reason=""):
        """クリック処理の状態をターミナルへ出します。

        縮尺設定中に点が打てない時、mode / toolbar / xdata / ydata を見ると
        どこで無視されたかを追いやすくなります。
        """

        debug_log(
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

        debug_log(
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

    def next_panel_y(self, y, height=0.034, gap=0.008):
        """右側パネルの次のボタン位置を返します。"""

        return y - height - gap

    def create_panel_title(self, label, y):
        """右側パネルのセクション見出しを作ります。"""

        return self.fig.text(
            0.835,
            y,
            label,
            fontsize=11.5,
            fontweight="bold",
            color="#222222",
        )

    def create_panel_button(self, label, y, callback=None, x=0.84, width=0.14, height=0.034):
        """右側パネル用のボタンを作ります。"""

        ax = self.register_ui_axes(self.fig.add_axes([x, y, width, height]))
        button = Button(ax, label)

        if callback is not None:
            button.on_clicked(callback)

        return button

    def create_panel_button_row(self, left_label, right_label, y, left_callback, right_callback):
        """右側パネル用に横並び2ボタンを作ります。"""

        left_button = self.create_panel_button(
            left_label,
            y,
            left_callback,
            width=0.066,
        )
        right_button = self.create_panel_button(
            right_label,
            y,
            right_callback,
            x=0.914,
            width=0.066,
        )

        return left_button, right_button

    def setup_widgets(self):
        """右側にレイヤ切り替えと主要操作ボタンを置きます。"""

        self.operation_title_text = self.create_panel_title("操作", 0.958)
        self.layer_title_text = self.create_panel_title("", 0.548)
        self.info_title_text = self.create_panel_title("情報", 0.318)
        self.scale_reference_text = self.fig.text(
            0.835,
            0.228,
            "",
            fontsize=8.0,
            color="#222222",
            va="top",
            linespacing=1.15,
        )

        y = 0.902
        self.save_button = self.create_panel_button(
            "DXF保存",
            y,
            self.safe_callback("save_dxf", lambda event: self.save_dxf()),
        )
        y = self.next_panel_y(y)
        self.project_save_button = self.create_panel_button(
            "作業保存",
            y,
            self.safe_callback("save_project", lambda event: self.save_project()),
        )
        y = self.next_panel_y(y)
        self.project_load_button = self.create_panel_button(
            "作業読込",
            y,
            self.safe_callback("load_project", lambda event: self.load_project()),
        )
        y = self.next_panel_y(y)
        self.scale_button = self.create_panel_button(
            "縮尺設定",
            y,
            self.safe_callback("start_scale_mode", lambda event: self.start_scale_mode()),
        )
        y = self.next_panel_y(y)
        self.gsi_settings_button = self.create_panel_button(
            "取得設定",
            y,
            self.safe_callback("toggle_gsi_settings", lambda event: self.toggle_gsi_settings_ui()),
        )
        y = self.next_panel_y(y)
        self.gsi_tile_button = self.create_panel_button(
            "地理院取得",
            y,
            self.safe_callback("fetch_gsi_tile", lambda event: self.fetch_default_gsi_tile()),
        )
        y = self.next_panel_y(y)
        self.close_button = self.create_panel_button(
            "閉じて確定",
            y,
            self.safe_callback(
                "finish_current_line_closed",
                lambda event: self.finish_current_line(closed=True),
            ),
        )
        y = self.next_panel_y(y)
        self.undo_button, self.redo_button = self.create_panel_button_row(
            "Undo",
            "Redo",
            y,
            self.safe_callback("undo", lambda event: self.undo()),
            self.safe_callback("redo", lambda event: self.redo()),
        )

        for index, (key, layer) in enumerate(LAYERS.items()):
            layer_y = 0.492 - index * 0.046
            button = self.create_panel_button(
                layer["label"],
                layer_y,
                self.safe_callback(
                    f"change_layer:{layer['label']}",
                    lambda event, layer_key=key: self.change_layer(layer_key),
                ),
            )
            self.layer_buttons[key] = button

        self.info_detail_button = self.create_panel_button(
            "詳細表示",
            0.263,
            self.safe_callback("toggle_info_detail", lambda event: self.toggle_info_detail()),
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
                f"レイヤ: {self.current_layer()['label']}"
            )

        if self.scale_reference_text is not None:
            self.scale_reference_text.set_text(
                self.get_reference_scale_message(detailed=self.info_detail_expanded)
            )

        if self.info_detail_button is not None:
            self.info_detail_button.label.set_text(
                "詳細を隠す" if self.info_detail_expanded else "詳細表示"
            )

        for key, button in self.layer_buttons.items():
            is_current = key == self.current_layer_key
            button.ax.set_facecolor("#ffe8e8" if is_current else "#f4f4f4")
            button.color = "#ffe8e8" if is_current else "#f4f4f4"
            button.hovercolor = "#ffd6d6" if is_current else "#e8e8e8"
            button.label.set_fontweight("bold" if is_current else "normal")
            button.label.set_color("#b00020" if is_current else "#222222")
            for spine in button.ax.spines.values():
                spine.set_edgecolor("#b00020" if is_current else "#bdbdbd")
                spine.set_linewidth(1.8 if is_current else 0.8)

    def toggle_info_detail(self):
        """右側情報欄の詳細表示を切り替えます。"""

        self.info_detail_expanded = not self.info_detail_expanded
        self.update_layer_ui()
        self.fig.canvas.draw_idle()

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
        debug_log(f"[event-connected] {self.event_cids}")

    def on_figure_close(self, event):
        """figure が閉じられた瞬間に理由追跡用ログを出します。"""

        debug_log(
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

        self.crosshair_horizontal = None
        self.crosshair_vertical = None
        self.crosshair_center = None
        self.ax.clear()
        self.ax.imshow(self.image)
        self.ax.set_aspect("equal", adjustable="box")

        self.ax.set_title(
            "左クリック=点追加/選択  右クリック=線確定  Space=移動  "
            "ホイール=ズーム  Esc=キャンセル",
            fontsize=9.5,
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
        self.draw_crosshair()
        self.draw_status_bar()
        self.update_layer_ui()
        self.fig.canvas.draw_idle()

        self.is_redrawing = False
        debug_log(
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

    def set_canvas_cursor(self, cursor):
        """バックエンド差を吸収しながらカーソル形状を切り替えます。"""

        try:
            self.fig.canvas.set_cursor(cursor)
        except Exception:
            pass

    def set_crosshair_cursor(self, enabled):
        """画像上では細かいトレース向けの十字カーソルにします。"""

        if enabled and self.crosshair_mode == "full":
            cursor = cursors.SELECT_REGION
        else:
            cursor = cursors.POINTER
        self.set_canvas_cursor(cursor)

    def is_full_crosshair_mode(self):
        """画面全体の十字線を出すモードかどうかを返します。"""

        return self.crosshair_mode == "full"

    def get_crosshair_event_point(self, event):
        """イベント位置から十字ガイド中心の画像座標を返します。

        small モードではOSカーソルと重ならないように表示中心を少し右下へずらし、
        そのずらした中心を実際の作図座標として扱います。
        """

        image_point = self.get_image_event_point(event)
        if image_point is None:
            return None

        if not self.crosshair_enabled or self.crosshair_mode != "small":
            return image_point

        if getattr(event, "x", None) is None or getattr(event, "y", None) is None:
            return image_point

        offset_x, offset_y = CROSSHAIR_SMALL_OFFSET_PX
        x, y = self.image_ax.transData.inverted().transform(
            (event.x + offset_x, event.y - offset_y)
        )

        return (
            min(max(x, 0), self.image.width),
            min(max(y, 0), self.image.height),
        )

    def get_crosshair_screen_point(self, event):
        """点選択判定用に、十字中心の画面座標を返します。"""

        point = self.get_crosshair_event_point(event)
        if point is None:
            return None

        return self.ax.transData.transform(point)

    def draw_crosshair(self):
        """マウス位置に追従する十字ガイドを描画します。"""

        if not self.crosshair_enabled or self.crosshair_image_point is None:
            return

        x, y = self.crosshair_image_point
        self.crosshair_horizontal = self.ax.axhline(
            y,
            color="#111111",
            linewidth=CROSSHAIR_LINE_WIDTH,
            alpha=0.50,
            linestyle="-",
            picker=False,
            visible=self.is_full_crosshair_mode(),
            zorder=28,
        )
        self.crosshair_vertical = self.ax.axvline(
            x,
            color="#111111",
            linewidth=CROSSHAIR_LINE_WIDTH,
            alpha=0.50,
            linestyle="-",
            picker=False,
            visible=self.is_full_crosshair_mode(),
            zorder=28,
        )
        self.crosshair_center = self.ax.plot(
            x,
            y,
            "+",
            color="#111111",
            markeredgecolor="#111111",
            markeredgewidth=1.4,
            markersize=CROSSHAIR_CENTER_SIZE,
            picker=False,
            zorder=29,
        )[0]
        self.crosshair_dot = self.ax.plot(
            x,
            y,
            "o",
            color="#fff45c",
            markeredgecolor="#111111",
            markeredgewidth=0.7,
            markersize=CROSSHAIR_DOT_SIZE,
            picker=False,
            zorder=30,
        )[0]

    def update_crosshair(self):
        """既存の十字ガイドを動かし、必要な時だけ再作成します。"""

        if not self.crosshair_enabled or self.crosshair_image_point is None:
            for artist in (
                self.crosshair_horizontal,
                self.crosshair_vertical,
                self.crosshair_center,
                self.crosshair_dot,
            ):
                if artist is not None:
                    artist.set_visible(False)
            return

        if (
            self.crosshair_horizontal is None
            or self.crosshair_vertical is None
            or self.crosshair_center is None
            or self.crosshair_dot is None
        ):
            self.draw_crosshair()
            return

        x, y = self.crosshair_image_point
        show_full_lines = self.is_full_crosshair_mode()
        self.crosshair_horizontal.set_visible(show_full_lines)
        self.crosshair_vertical.set_visible(show_full_lines)
        self.crosshair_center.set_visible(True)
        self.crosshair_dot.set_visible(True)
        self.crosshair_horizontal.set_ydata([y, y])
        self.crosshair_vertical.set_xdata([x, x])
        self.crosshair_center.set_data([x], [y])
        self.crosshair_dot.set_data([x], [y])

    def should_draw_crosshair_now(self):
        """マウス移動時の描画頻度を抑えてちらつきを減らします。"""

        now = time.monotonic()
        interval = CROSSHAIR_UPDATE_INTERVAL_MS / 1000
        if now - self.crosshair_last_draw_at < interval:
            return False

        self.crosshair_last_draw_at = now
        return True

    def toggle_crosshair(self):
        """xキーで十字ガイドの表示を切り替えます。"""

        self.crosshair_enabled = not self.crosshair_enabled
        if not self.crosshair_enabled:
            self.set_crosshair_cursor(False)
        elif self.mouse_image_point is not None:
            self.set_crosshair_cursor(True)
            self.crosshair_image_point = self.get_active_drawing_point()

        print(
            f"十字ガイド: {'ON' if self.crosshair_enabled else 'OFF'} "
            f"({self.crosshair_mode})"
        )
        self.update_crosshair()
        self.draw_status_bar()
        self.fig.canvas.draw_idle()

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
                self.ax.plot(
                    xs,
                    ys,
                    "-",
                    color=color,
                    linewidth=CONFIRMED_LINE_WIDTH,
                    zorder=10,
                )

                if is_closed:
                    self.ax.fill(xs, ys, color=color, alpha=0.12)

            for point_index, (x, y) in enumerate(pts):
                marker = "o"
                marker_size = CONFIRMED_MARKER_SIZE
                marker_color = color

                if self.selected_point == ("line", line_index, point_index):
                    marker = "s"
                    marker_size = SELECTED_MARKER_SIZE
                    marker_color = "#000000"

                self.ax.plot(
                    x,
                    y,
                    marker,
                    color=marker_color,
                    markeredgecolor="#ffffff",
                    markeredgewidth=MARKER_EDGE_WIDTH,
                    markersize=marker_size,
                    zorder=11,
                )

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
                linewidth=CURRENT_LINE_WIDTH,
                zorder=12,
            )

        for point_index, (x, y) in enumerate(self.current_points):
            marker = "o"
            marker_size = CURRENT_MARKER_SIZE
            marker_color = color

            if self.selected_point == ("current", point_index):
                marker = "s"
                marker_size = SELECTED_MARKER_SIZE
                marker_color = "#000000"

            self.ax.plot(
                x,
                y,
                marker,
                color=marker_color,
                markeredgecolor="#ffffff",
                markeredgewidth=MARKER_EDGE_WIDTH,
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
                markeredgewidth=MARKER_EDGE_WIDTH,
                markersize=SCALE_MARKER_SIZE,
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
                linewidth=SCALE_LINE_WIDTH,
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

        image_point = self.get_crosshair_event_point(event)

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
        debug_log(
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
        """マウス移動時に十字ガイド、座標表示、Panを更新します。"""

        image_point = self.get_image_event_point(event)
        self.mouse_image_point = image_point
        self.crosshair_image_point = self.get_crosshair_event_point(event)
        self.set_crosshair_cursor(image_point is not None and self.crosshair_enabled)
        self.update_crosshair()
        self.draw_status_bar()
        should_draw = self.should_draw_crosshair_now()

        if not self.pan_active:
            if should_draw:
                self.fig.canvas.draw_idle()
            return

        if event.x is None or event.y is None:
            if should_draw:
                self.fig.canvas.draw_idle()
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

        self.update_crosshair()
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

        self.update_crosshair()
        self.draw_status_bar()
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

        if key in ("ctrl+s", "cmd+s"):
            self.save_project()
            return

        if key in ("ctrl+o", "cmd+o"):
            self.load_project()
            return

        if key in ("delete", "backspace"):
            self.delete_selected_point()
            return

        if key == "k":
            self.start_scale_mode()
            return

        if key == "x":
            self.toggle_crosshair()
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
        debug_log(
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

        click_xy = self.get_crosshair_screen_point(event)
        if click_xy is None:
            return None

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
        debug_log(f"現在の interaction_mode: {self.interaction_mode}")
        debug_log(f"現在の toolbar.mode: {self.get_toolbar_mode()}")

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
        self.scale_needs_reset = False
        self.scale_reset_message = ""
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

        debug_log("")
        debug_log("[scale-complete]")
        debug_log(f"interaction_mode={self.interaction_mode}")
        debug_log(f"scale_mode={self.scale_mode}")
        debug_log(f"scale_points={len(self.scale_points)}")
        debug_log(f"scale_input_box={self.scale_input_box}")
        debug_log(f"scale_ok_button={self.scale_ok_button}")
        debug_log(f"current_layer={self.current_layer()['label']}")
        debug_log(f"success_message_active={self.is_notice_active()}")
        debug_log(f"redraw_scheduled={self.redraw_scheduled}")
        debug_log(f"is_redrawing={self.is_redrawing}")

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

    def save_dxf(self):
        """現在の図形をDXFとして保存します。"""

        if len(self.current_points) >= 2:
            self.finish_current_line(closed=False)

        filename = export_dxf(
            lines=self.lines,
            background_image_path=self.background_image_path,
            image_width=self.image.width,
            image_height=self.image.height,
            meters_per_pixel=self.meters_per_pixel,
        )

        print("")
        print(f"DXF保存完了: {filename}")
        print(f"保存時縮尺: 1px = {self.meters_per_pixel:.4f}m")
        print(
            "背景画像は外部参照です。DXFと "
            f"{os.path.basename(self.background_image_path)} "
            "を同じフォルダで管理してください"
        )

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
        print("Cmd+S / Ctrl+S: project.json保存")
        print("Cmd+O / Ctrl+O: project.json読込")
        print("n: 線として確定")
        print("c: 閉じた面として確定")
        print("d: 選択点を削除。選択がなければ最後の線を削除")
        print("k: 縮尺設定")
        print("x: 十字ガイド ON/OFF")
        print("1: 道路 / 2: 敷地 / 3: 法面 / 4: 構造物")
        print("Enter: DXF保存")
        print("q: 終了")
        print("================")


def main():
    configure_matplotlib_fonts()
    disable_conflicting_matplotlib_shortcuts()
    app = SimpleCadApp()
    try:
        debug_log(
            "[mainloop-start] "
            f"manager_exists={app.fig.canvas.manager is not None} | "
            f"fignum_exists={plt.fignum_exists(app.fig.number)}"
        )
        plt.show()
    finally:
        debug_log(
            "[mainloop-ended] "
            f"manager_exists={app.fig.canvas.manager is not None} | "
            f"fignum_exists={plt.fignum_exists(app.fig.number)} | "
            f"interaction_mode={app.interaction_mode} | "
            f"current_layer={app.current_layer()['label']} | "
            f"current_points={len(app.current_points)}"
        )


if __name__ == "__main__":
    main()
