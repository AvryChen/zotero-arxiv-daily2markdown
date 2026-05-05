import arxiv
search = arxiv.Search(
  query = 'lastUpdatedDate:[202405010000 TO 202405020000]',
  max_results = 5
)
client = arxiv.Client()
results = list(client.results(search))
for r in results:
    print(r.title, r.published)
