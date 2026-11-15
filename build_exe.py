"""
打包 vgrade 桌面程式（Windows）。

    pip install pyinstaller tkinterdnd2 yowasp-yosys
    python build_exe.py

產出：
    dist/vgrade/vgrade.exe     可直接執行的資料夾版
    dist/vgrade-win64.zip      發佈用壓縮檔
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> int:
    ico = ROOT / "assets" / "vgrade.ico"
    if not ico.exists():
        subprocess.check_call([sys.executable, str(ROOT / "tools" / "make_icon.py")])

    args = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--name", "vgrade",
        "--windowed",                         # 不顯示主控台視窗
        "--onedir",                           # 資料夾版：啟動快（Yosys 有 75 MB，不適合 onefile）
        "--icon", str(ico),
        "--add-data", f"{ico};assets",
        "--collect-all", "yowasp_yosys",      # yosys.wasm 與 share/ 資料
        "--collect-all", "yowasp_runtime",
        "--collect-all", "wasmtime",          # 原生 DLL
        "--collect-all", "tkinterdnd2",       # 拖放功能的 tkdnd 函式庫
        "--hidden-import", "report_html",
        str(ROOT / "vgrade_app.py"),
    ]
    subprocess.check_call(args, cwd=ROOT)

    dist = ROOT / "dist" / "vgrade"
    shutil.copy(ROOT / "LICENSE", dist / "LICENSE.txt")
    (dist / "使用說明.txt").write_text(
        "vgrade - Verilog 程式碼品質評分\n\n"
        "1. 執行 vgrade.exe\n"
        "2. 把 .v / .sv 檔或整個 Vivado 專案資料夾拖進視窗\n"
        "3. 按「開始評分」，完成後會自動開啟 HTML 評分報告\n\n"
        "第一次評分時需要編譯內建的 Yosys，約需 30 秒到 1 分鐘，之後就會很快。\n"
        "若電腦有安裝 Icarus Verilog（C:\\iverilog），會自動用於語法檢查。\n\n"
        "專案網址：https://github.com/WANG-JUI-CHAO/vgrade\n",
        encoding="utf-8-sig")
    archive = shutil.make_archive(str(ROOT / "dist" / "vgrade-win64"), "zip", ROOT / "dist", "vgrade")
    print(f"\n完成：{dist / 'vgrade.exe'}\n發佈檔：{archive}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
