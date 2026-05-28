import json
from collections import defaultdict

with open('Output Files/sample_lineage.json', encoding='utf-8') as f:
    data = json.load(f)

vertices = {v['id']: v for v in data['vertices']}
edges = data['edges']

# Build in_edges map: to_vertex -> list of edges
in_edges = defaultdict(list)
for e in edges:
    in_edges[e['to_vertex']].append(e)

# Recursive trace upstream
def trace_upstream(vertex_id, visited=None, depth=0):
    if visited is None:
        visited = set()
    if vertex_id in visited:
        return []
    visited.add(vertex_id)

    v = vertices.get(vertex_id)
    if not v:
        return [{'id': vertex_id, 'layer': '?', 'incoming_edges': [], 'depth': depth}]

    incoming = in_edges.get(vertex_id, [])

    node_info = {
        'id': vertex_id,
        'layer': v.get('layer', '?'),
        'db_schema': v.get('db_schema', ''),
        'table_name': v.get('table_name', ''),
        'field_name': v.get('field_name', ''),
        'depth': depth,
        'incoming_edges': []
    }

    result = [node_info]

    for edge in incoming:
        edge_info = {
            'from_vertex': edge['from_vertex'],
            'from_layer': vertices.get(edge['from_vertex'], {}).get('layer', '?'),
            'mapping_name': edge.get('mapping_name', ''),
            'expression': edge.get('expression', '')
        }
        node_info['incoming_edges'].append(edge_info)
        # Recurse upstream
        upstream = trace_upstream(edge['from_vertex'], visited, depth + 1)
        result.extend(upstream)

    return result


def print_chain(chain):
    for node in chain:
        indent = '  ' * node['depth']
        print(f"\n{indent}[{node['layer']}] {node['id']}")
        for edge in node['incoming_edges']:
            print(f"{indent}  <-- from [{edge['from_layer']}] {edge['from_vertex']}")
            print(f"{indent}      mapping: {edge['mapping_name']}")
            print(f"{indent}      expr:    {edge['expression']}")


# Trace from target
target = 'CRDM_DDM.F_PARTICIPANTS.PARTICIPANT_KEY'
print('=' * 80)
print(f'UPSTREAM LINEAGE FOR: {target}')
print('=' * 80)

chain = trace_upstream(target)
if not chain or (len(chain) == 1 and not chain[0]['incoming_edges']):
    print(f"\nNo incoming edges found for {target}")
else:
    print_chain(chain)

# Also check intermediate
print('\n' + '=' * 80)
intermediate = 'CRDM_TMP.TT_F_PARTICIPANTS.PARTICIPANT_KEY'
print(f'UPSTREAM LINEAGE FOR INTERMEDIATE: {intermediate}')
print('=' * 80)

chain2 = trace_upstream(intermediate, visited=set())
if not chain2 or (len(chain2) == 1 and not chain2[0]['incoming_edges']):
    print(f"\nNo incoming edges found for {intermediate}")
else:
    print_chain(chain2)

# Show if target exists in vertices
print('\n' + '=' * 80)
print('VERTEX EXISTENCE CHECK:')
print(f"  {target}: {'EXISTS' if target in vertices else 'NOT FOUND'}")
print(f"  {intermediate}: {'EXISTS' if intermediate in vertices else 'NOT FOUND'}")

# Show all edges TO these vertices
print('\nAll edges TO target:')
for e in edges:
    if e['to_vertex'] == target:
        print(f"  from: {e['from_vertex']} | mapping: {e.get('mapping_name','')} | expr: {e.get('expression','')}")

print(f'\nAll edges TO intermediate:')
for e in edges:
    if e['to_vertex'] == intermediate:
        print(f"  from: {e['from_vertex']} | mapping: {e.get('mapping_name','')} | expr: {e.get('expression','')}")

# Show all edges FROM intermediate (downstream)
print(f'\nAll edges FROM intermediate (downstream):')
for e in edges:
    if e['from_vertex'] == intermediate:
        print(f"  to: {e['to_vertex']} | mapping: {e.get('mapping_name','')} | expr: {e.get('expression','')}")
