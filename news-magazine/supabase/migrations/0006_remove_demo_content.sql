-- Remove legacy scaffold fixtures if they were applied to a hosted database.

delete from articles where slug = 'demo-article-one';
delete from authors where slug = 'demo-staff-writer';
delete from sources where name = 'Demo RSS Source [DEMO DATA]';
