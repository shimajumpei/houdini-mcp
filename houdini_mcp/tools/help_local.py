"""Local Japanese help file parser for Houdini nodes.

Parses .txt help files from ~/Documents/houdini20.5/help/nodes/ to extract
Japanese parameter descriptions. These are integrated into get_parameter_schema
and get_node_info when include_description_ja=True.

Help files use a wiki-like markup with @parameters sections containing
indentation-based parameter blocks, #id: tags for internal names, and
#channels: tags for channel name mapping.
"""

import logging
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("houdini_mcp.tools.help_local")

# Default help base path
_DEFAULT_HELP_PATH = os.path.join(
    os.path.expanduser("~"), "Documents", "houdini20.5", "help"
)

# Category mapping: Houdini node category name → help directory name
CATEGORY_MAP: Dict[str, str] = {
    "Sop": "sop",
    "Object": "obj",
    "Driver": "out",
    "Dop": "dop",
    "Vop": "vop",
    "Cop2": "cop2",
    "Chop": "chop",
    "Shop": "shop",
    "Lop": "lop",
    "Top": "top",
    "Cop": "cop",
    "Apex": "apex",
    # Lowercase fallbacks
    "sop": "sop",
    "obj": "obj",
    "out": "out",
    "dop": "dop",
    "vop": "vop",
    "cop2": "cop2",
    "chop": "chop",
    "shop": "shop",
    "lop": "lop",
    "top": "top",
    "cop": "cop",
    "apex": "apex",
}


@dataclass
class ParameterHelpEntry:
    """A single parameter's help information extracted from a help file."""

    label: str
    param_id: Optional[str] = None
    channel_names: List[str] = field(default_factory=list)
    description_ja: str = ""
    menu_items_ja: Dict[str, str] = field(default_factory=dict)


@dataclass
class ParsedNodeHelp:
    """Parsed help data for a single node type."""

    context: str
    internal_name: str
    title: str
    summary_ja: str
    parameters: List[ParameterHelpEntry] = field(default_factory=list)
    _by_id: Dict[str, ParameterHelpEntry] = field(default_factory=dict)
    _by_channel: Dict[str, ParameterHelpEntry] = field(default_factory=dict)
    _by_label: Dict[str, ParameterHelpEntry] = field(default_factory=dict)

    def _build_indices(self) -> None:
        """Build lookup indices from the parameters list."""
        self._by_id = {}
        self._by_channel = {}
        self._by_label = {}
        for entry in self.parameters:
            if entry.param_id:
                self._by_id[entry.param_id] = entry
            for ch in entry.channel_names:
                self._by_channel[ch] = entry
            label_key = entry.label.strip().lower()
            if label_key:
                self._by_label[label_key] = entry

    def lookup(
        self, param_name: str, param_label: str = ""
    ) -> Optional[ParameterHelpEntry]:
        """Look up a parameter help entry by name/label.

        Priority: #id match > #channels match > label match.
        """
        # 1. Direct #id match
        if param_name in self._by_id:
            return self._by_id[param_name]

        # 2. Channel name match (channels store names like "sizex", "tx")
        if param_name in self._by_channel:
            return self._by_channel[param_name]

        # 3. Label match (case-insensitive)
        if param_label:
            label_key = param_label.strip().lower()
            if label_key in self._by_label:
                return self._by_label[label_key]

        return None


def _get_help_base_path() -> str:
    """Get the help base path from environment or default."""
    return os.environ.get("HOUDINI_HELP_PATH", _DEFAULT_HELP_PATH)


def _resolve_help_file(context: str, type_name: str) -> Optional[Path]:
    """Resolve the help file path for a given node context and type name.

    Args:
        context: Houdini node category (e.g., "Sop", "Object", "Driver")
        type_name: Node type name (e.g., "box", "attribcreate::2.0", "labs::foo")

    Returns:
        Path to the help file, or None if not found.
    """
    base_path = Path(_get_help_base_path())
    help_dir_name = CATEGORY_MAP.get(context)
    if not help_dir_name:
        # Try lowercase
        help_dir_name = CATEGORY_MAP.get(context.lower(), context.lower())

    nodes_dir = base_path / "nodes" / help_dir_name

    if not nodes_dir.is_dir():
        return None

    # Parse type_name for namespace and version
    # Examples: "box", "attribcreate::2.0", "labs::foo", "labs::av_analyze_images::3.0"
    namespace = ""
    base_name = type_name
    version = ""

    # Split namespace (first ::)
    if "::" in type_name:
        parts = type_name.split("::")
        if len(parts) == 2:
            # Could be namespace::name or name::version
            # If the second part looks like a version number, treat it as name::version
            if re.match(r"^\d+(\.\d+)?$", parts[1]):
                base_name = parts[0]
                version = parts[1]
            else:
                namespace = parts[0]
                base_name = parts[1]
        elif len(parts) >= 3:
            # namespace::name::version
            namespace = parts[0]
            base_name = parts[1]
            version = parts[2]

    # Build candidate filenames
    candidates: List[str] = []

    if namespace:
        # Namespaced node: labs::foo → labs--foo.txt
        ns_name = f"{namespace}--{base_name}"
        if version:
            # Try versioned first: labs--foo-3.0.txt
            candidates.append(f"{ns_name}-{version}.txt")
        candidates.append(f"{ns_name}.txt")
    else:
        # Non-namespaced node
        name_lower = base_name.lower()
        if version:
            # Versioned: attribcreate::2.0 → try attribcreate.txt first, then attribcreate-2.0.txt
            candidates.append(f"{name_lower}.txt")
            candidates.append(f"{name_lower}-{version}.txt")
        else:
            candidates.append(f"{name_lower}.txt")

    # Also try with a trailing dash variant (e.g., attribcreate-.txt)
    if not namespace and not version:
        candidates.append(f"{base_name.lower()}-.txt")

    for candidate in candidates:
        file_path = nodes_dir / candidate
        if file_path.is_file():
            return file_path

    return None


def _parse_help_file(file_path: Path) -> Optional[ParsedNodeHelp]:
    """Parse a Houdini help .txt file.

    Args:
        file_path: Path to the help file.

    Returns:
        ParsedNodeHelp with extracted data, or None on failure.
    """
    try:
        text = file_path.read_text(encoding="utf-8-sig")
    except Exception as e:
        logger.warning(f"Failed to read help file {file_path}: {e}")
        return None

    lines = text.splitlines()

    # Extract header metadata
    title = ""
    context = ""
    internal_name = ""
    summary_ja = ""

    # Parse title from first non-empty line: = Title =
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("= ") and stripped.endswith(" ="):
            title = stripped[2:-2].strip()
            break

    # Parse metadata tags
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#context:"):
            context = stripped[len("#context:"):].strip()
        elif stripped.startswith("#internal:"):
            internal_name = stripped[len("#internal:"):].strip()

    # Extract summary from triple-quoted string: """summary"""
    summary_match = re.search(r'"""(.+?)"""', text, re.DOTALL)
    if summary_match:
        summary_ja = summary_match.group(1).strip()

    # Parse @parameters section
    parameters = _parse_parameters_section(lines)

    result = ParsedNodeHelp(
        context=context,
        internal_name=internal_name,
        title=title,
        summary_ja=summary_ja,
        parameters=parameters,
    )
    result._build_indices()
    return result


def _parse_parameters_section(lines: List[str]) -> List[ParameterHelpEntry]:
    """Parse the @parameters section of a help file.

    The format is indentation-based with two common styles:
    - box.txt style: param labels at indent 4, metadata/desc at 8, menu items at 8, menu desc at 12
    - scatter.txt style: param labels at indent 0, metadata/desc at 4, menu items at 4, menu desc at 8

    The parser auto-detects the base indentation level from the first label line.

    Returns:
        List of ParameterHelpEntry objects.
    """
    # Find @parameters section
    params_start = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("@parameters"):
            params_start = i + 1
            break

    if params_start < 0:
        return []

    # Find end of @parameters section (next @section or end of file)
    params_end = len(lines)
    for i in range(params_start, len(lines)):
        stripped = lines[i].strip()
        if stripped.startswith("@") and not stripped.startswith("@parameters"):
            params_end = i
            break

    # Auto-detect base indentation: find first non-empty, non-metadata line
    # that looks like a label (ends with :)
    base_indent = -1
    for i in range(params_start, params_end):
        line = lines[i]
        raw = line.strip()
        if not raw or raw.startswith("#") or raw.startswith(":"):
            continue
        expanded = line.replace("\t", "    ")
        indent = len(expanded) - len(expanded.lstrip())
        if re.match(r"^.+:\s*$", raw):
            base_indent = indent
            break

    if base_indent < 0:
        base_indent = 0  # fallback

    # Indent thresholds relative to base
    indent_param = base_indent           # parameter label level
    indent_content = base_indent + 4     # metadata / description / menu item labels
    indent_menu_desc = base_indent + 8   # menu item descriptions

    # Parse parameter blocks
    parameters: List[ParameterHelpEntry] = []
    current_entry: Optional[ParameterHelpEntry] = None
    current_menu_label: Optional[str] = None
    current_menu_desc_lines: List[str] = []
    desc_lines: List[str] = []
    in_note_block = False
    note_block_indent = 0

    def _flush_menu_item():
        nonlocal current_menu_label, current_menu_desc_lines
        if current_entry and current_menu_label and current_menu_desc_lines:
            current_entry.menu_items_ja[current_menu_label] = " ".join(
                current_menu_desc_lines
            ).strip()
        current_menu_label = None
        current_menu_desc_lines = []

    def _flush_entry():
        nonlocal current_entry, desc_lines, in_note_block
        _flush_menu_item()
        if current_entry is not None:
            if desc_lines:
                current_entry.description_ja = " ".join(desc_lines).strip()
            parameters.append(current_entry)
        current_entry = None
        desc_lines = []
        in_note_block = False

    for i in range(params_start, params_end):
        line = lines[i]
        raw_stripped = line.strip()

        # Skip empty lines
        if not raw_stripped:
            continue

        # Skip :include, :vimeo:, :task: directives
        if raw_stripped.startswith(":") and not raw_stripped.startswith("::"):
            continue

        # Compute indent
        expanded = line.replace("\t", "    ")
        indent = len(expanded) - len(expanded.lstrip())

        is_metadata = raw_stripped.startswith("#id:") or raw_stripped.startswith(
            "#channels:"
        )

        # Handle NOTE:/TIP:/WARNING: blocks - skip until indent drops
        if raw_stripped in ("NOTE:", "TIP:", "WARNING:"):
            in_note_block = True
            note_block_indent = indent
            continue

        if in_note_block:
            if indent <= note_block_indent and not is_metadata:
                # Check if this line starts a new parameter label
                if re.match(r"^.+:\s*$", raw_stripped):
                    in_note_block = False
                    # Fall through to process
                else:
                    continue
            else:
                continue

        # Process metadata tags
        if is_metadata and current_entry is not None:
            if raw_stripped.startswith("#id:"):
                current_entry.param_id = raw_stripped[len("#id:"):].strip()
            elif raw_stripped.startswith("#channels:"):
                channels_str = raw_stripped[len("#channels:"):].strip()
                current_entry.channel_names = [
                    ch.lstrip("/") for ch in channels_str.split() if ch
                ]
            continue

        # Check for label lines (ends with ":", possibly with parenthetical)
        label_match = re.match(r"^(.+?)(?:\(.*?\))?:\s*$", raw_stripped)

        if label_match and not is_metadata:
            label_text = label_match.group(0).rstrip(":").strip()
            clean_label = re.sub(r"\(.*?\)\s*$", "", label_text).strip()

            if indent <= indent_param:
                # Top-level parameter label
                _flush_entry()
                current_entry = ParameterHelpEntry(label=clean_label)
                desc_lines = []
                current_menu_label = None
                current_menu_desc_lines = []
            elif current_entry is not None and indent >= indent_content:
                # Menu item label (at content indent level)
                _flush_menu_item()
                current_menu_label = clean_label
                current_menu_desc_lines = []
            continue

        # Description or menu description text
        if current_entry is not None:
            if current_menu_label is not None:
                # Collecting menu item description
                current_menu_desc_lines.append(raw_stripped)
            elif indent >= indent_content:
                # Regular parameter description
                desc_lines.append(raw_stripped)

    # Flush last entry
    _flush_entry()

    return parameters


# =============================================================================
# Cache
# =============================================================================

_help_cache: Dict[Tuple[str, str], Optional[ParsedNodeHelp]] = {}
_help_cache_lock = threading.RLock()


def get_local_help(context: str, type_name: str) -> Optional[ParsedNodeHelp]:
    """Get parsed help for a node type, with caching.

    Args:
        context: Houdini node category (e.g., "Sop", "Object")
        type_name: Node type name (e.g., "box", "attribcreate::2.0")

    Returns:
        ParsedNodeHelp or None if no help file found.
    """
    cache_key = (context, type_name)

    with _help_cache_lock:
        if cache_key in _help_cache:
            return _help_cache[cache_key]

    # Parse outside the lock to avoid blocking other threads
    file_path = _resolve_help_file(context, type_name)
    result: Optional[ParsedNodeHelp] = None
    if file_path is not None:
        result = _parse_help_file(file_path)
        if result:
            logger.debug(
                f"Parsed help for {context}/{type_name}: "
                f"{len(result.parameters)} parameters"
            )

    with _help_cache_lock:
        _help_cache[cache_key] = result

    return result


def invalidate_help_cache() -> None:
    """Clear the help file cache."""
    with _help_cache_lock:
        _help_cache.clear()
    logger.debug("Help cache invalidated")
