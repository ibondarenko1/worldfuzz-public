"""Grid search candidates only. Outcomes belong to the separate verifier."""
import math
import heapq

def solve(rows, case, checkpoint, algorithm="astar"):
    if algorithm not in ("astar","dijkstra"):raise ValueError("Unsupported solver")
    start,goal=tuple(case['start']),tuple(case['goal'])
    def heuristic(p):
        if algorithm=="dijkstra":return 0.0
        dx,dy=abs(p[0]-goal[0]),abs(p[1]-goal[1])
        return max(dx,dy)+(math.sqrt(2)-1)*min(dx,dy)
    frontier=[(heuristic(start),0.0,start)]
    costs={start:0.0};parents={};expanded=0
    while frontier:
        _,cost,current=heapq.heappop(frontier)
        if cost>costs[current]:continue
        if current==goal:
            route=[list(current)]
            while current!=start:
                current=parents[current];route.append(list(current))
            return route[::-1]
        expanded+=1
        if expanded%256==0:checkpoint()
        x,y=current
        for dx,dy in ((1,0),(-1,0),(0,1),(0,-1),(1,1),(1,-1),(-1,1),(-1,-1)):
            nx,ny=x+dx,y+dy
            if not 0<=nx<case['width'] or not 0<=ny<case['height'] or rows[ny][nx]!='.':continue
            if dx and dy and (rows[y][nx]!='.' or rows[ny][x]!='.'):continue
            next_cost=cost+(math.sqrt(2) if dx and dy else 1)
            if next_cost<costs.get((nx,ny),math.inf):
                costs[nx,ny]=next_cost;parents[nx,ny]=current
                heapq.heappush(frontier,(next_cost+heuristic((nx,ny)),next_cost,(nx,ny)))
    return []

