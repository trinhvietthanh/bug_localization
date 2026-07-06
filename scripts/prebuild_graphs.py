import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from rich.console import Console

from rag.code_graph import get_or_build_graph
from config import config

console = Console()

def get_git_commit(repo_path: str) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except Exception:
        return ""

def main():
    checkout_root = "data/swebench_checkouts"
    if not os.path.exists(checkout_root):
        console.print("[red]Thư mục checkouts không tồn tại![/red]")
        return

    # Find all unique (repo_name, commit) combinations
    unique_repos = {}  # (repo_name, commit) -> instance_path
    
    console.print("[cyan]Đang quét các instance đã checkout để tìm các graph cần build...[/cyan]")
    for repo_dir in os.listdir(checkout_root):
        repo_path = os.path.join(checkout_root, repo_dir)
        if not os.path.isdir(repo_path):
            continue
            
        for instance_dir in os.listdir(repo_path):
            instance_path = os.path.join(repo_path, instance_dir)
            if not os.path.isdir(instance_path):
                continue
                
            commit = get_git_commit(instance_path)
            if commit:
                key = (repo_dir, commit)
                if key not in unique_repos:
                    unique_repos[key] = instance_path

    console.print(f"[green]Tìm thấy {len(unique_repos)} phiên bản code (commit) khác nhau cần build graph.[/green]")
    
    if not config.neo4j.enabled:
        console.print("[red]Neo4j chưa được bật trong .env (ENABLE_GRAPH_RAG=true)[/red]")
        return
        
    for (repo_name, commit), instance_path in unique_repos.items():
        repo_id = f"{repo_name}|python|{commit}"
        console.print(f"\\n[yellow]Đang build Graph cho:[/yellow] {repo_id}")
        console.print(f"Path: {instance_path}")
        
        try:
            graph = get_or_build_graph(
                repo_path=instance_path,
                language="python",  # SWE-bench is python
                use_neo4j=True,
                neo4j_uri=config.neo4j.uri,
                neo4j_user=config.neo4j.user,
                neo4j_password=config.neo4j.password,
                neo4j_database=config.neo4j.database,
                repo_id=repo_id,
            )
            console.print(f"[green]✅ Build thành công {repo_id}[/green]")
        except Exception as e:
            console.print(f"[red]❌ Lỗi khi build {repo_id}: {e}[/red]")

if __name__ == "__main__":
    main()
