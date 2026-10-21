SummitFlow SEO implementation package

Files:
- index.html: updated homepage title, description, canonical, social metadata, structured data, and H1.
- robots.txt: permits crawling and points crawlers to the sitemap.
- sitemap.xml: lists the homepage and privacy URL based on the URLs seen in Search Console.

Before deploying:
1. Confirm the live privacy URL. Search Console showed /privacy, while the supplied homepage footer links to /privacy.html. Update sitemap.xml and the footer link so they match the actual live URL.
2. Confirm the canonical domain is https://summitflow.co.uk/ and that HTTP/www variants redirect to it.
3. Deploy all files to the public web root.
4. Test the live homepage and files, then submit https://summitflow.co.uk/sitemap.xml in Search Console.
5. Use URL Inspection to request recrawling after deployment.

SEO cannot guarantee #1 rankings. These changes improve technical clarity and relevance; rankings depend on competition, search demand, quality, authority, and time.
