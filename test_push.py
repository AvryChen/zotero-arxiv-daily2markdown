import os
import shutil
import subprocess
from dotenv import load_dotenv

def test_auto_push():
    # 加载 .env 中的环境变量
    load_dotenv()
    
    # 获取 .env 中的配置
    output_dir = os.environ.get("HUGO_OUTPUT_DIR")
    if not output_dir:
        print("❌ 错误：在 .env 文件中未找到 HUGO_OUTPUT_DIR 变量。")
        return

    # 源文件：刚才生成的 md 文件
    source_file = "./hugo_output/2026-05-04-arxiv-daily.md"
    if not os.path.exists(source_file):
        print(f"❌ 错误：找不到源文件 {source_file}。请先执行主程序生成一次。")
        return

    # 目标文件：复制到你的 Hugo 目录里
    target_file = os.path.join(output_dir, "2026-05-04-arxiv-daily.md")
    
    print(f"📁 目标目录: {output_dir}")
    
    # 如果目标目录不是原目录，我们把文件拷贝过去装作是新生成的
    if os.path.abspath(source_file) != os.path.abspath(target_file):
        print(f"📄 正在模拟生成文件：将 md 文件拷贝至 {target_file}...")
        os.makedirs(output_dir, exist_ok=True)
        shutil.copy2(source_file, target_file)
    else:
        print("📄 目标目录与源目录相同，跳过拷贝。")

    print("\n🚀 开始执行 Git 推送流程...")
    try:
        # 1. git add
        print(">> 执行: git add")
        subprocess.run(["git", "add", target_file], cwd=output_dir, check=True)
        
        # 2. git commit (忽略如果没有改动的报错)
        commit_msg = "Auto: Add arXiv daily for 2026-05-04 (Test)"
        print(f">> 执行: git commit -m '{commit_msg}'")
        subprocess.run(["git", "commit", "-m", commit_msg], cwd=output_dir)
        
        # 3. git pull (非常关键：同步远端改动，且自动暂存你本地未提交的其他文件修改)
        print(">> 执行: git pull --rebase --autostash")
        subprocess.run(["git", "pull", "--rebase", "--autostash"], cwd=output_dir, check=True)
        
        # 4. git push
        print(">> 执行: git push")
        subprocess.run(["git", "push"], cwd=output_dir, check=True)
        
        print("\n✅ 测试成功！Markdown 已经推送到 GitHub！")
    except subprocess.CalledProcessError as e:
        print(f"\n❌ Git 命令执行失败。原因: {e}")
        print("提示：请检查 HUGO_OUTPUT_DIR 是否确实是一个 Git 仓库，且是否已配置了免密 push。")
    except Exception as e:
        print(f"\n❌ 发生了意外错误: {e}")

if __name__ == "__main__":
    test_auto_push()
