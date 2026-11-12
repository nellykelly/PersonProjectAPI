"""Fake legacy data loaded into RAW. Invented names; nothing real."""

RAW_DATA_SQL = """
create schema raw;

create table raw.pn_household (household_nbr integer, first_nm varchar, last_nm varchar, addr_line_1 varchar, st_cd integer);
insert into raw.pn_household values
    (50010, 'Dana',   'Whitfield', '114 Cedar Ln',      29),
    (50011, 'Marcus', 'Bell',      '9 Rolling Hills Dr', 21),
    (50012, 'Priya',  'Raman',     '402 Elm St',        21),
    (50013, 'Tom',    'Okafor',    '77 Lakeview Ct',    29),
    (50014, 'Lena',   'Duarte',    '1820 Pike Rd',      21),
    (50015, 'Sam',    'Ito',       '5 Harbor Way',      47);

create table raw.pn_policy (pol_nbr varchar, term_nbr integer, household_nbr integer, st_cd integer,
                                   pol_eff_dt date, pol_exp_dt date, pol_sts_cd varchar);
insert into raw.pn_policy values
    ('PN1001', 1, 50010, 29, '2025-06-01', '2025-12-01', 'E'),
    ('PN1001', 2, 50010, 29, '2025-12-01', '2026-06-01', 'E'),
    ('PN1001', 3, 50010, 29, '2026-06-01', '2026-12-01', 'A'),
    ('PN1002', 1, 50011, 21, '2026-03-15', '2026-09-15', 'E'),
    ('PN1002', 2, 50011, 21, '2026-09-15', '2027-03-15', 'A'),
    ('PN1003', 1, 50012, 21, '2026-08-01', '2027-02-01', 'A'),
    ('PN1004', 1, 50010, 29, '2026-04-20', '2026-10-20', 'E'),
    ('PN1004', 2, 50010, 29, '2026-10-20', '2027-04-20', 'A'),
    ('PN1005', 1, 50013, 29, '2026-05-01', '2026-11-01', 'C'),
    ('PN1006', 1, 50014, 21, '2026-07-10', '2027-01-10', 'A');

create table raw.pn_vehicle (pol_nbr varchar, veh_seq integer, vin varchar, mdl_yr integer, make_nm varchar);
insert into raw.pn_vehicle values
    ('PN1001', 1, '1HGCM82633A004352', 2019, 'Honda'),
    ('PN1001', 2, '1FTFW1ET5DFC10312', 2013, 'Ford'),
    ('PN1002', 1, '4T1BF1FK5CU123456', 2012, 'Toyota'),
    ('PN1003', 1, 'JF1GPAA61F8234567', 2015, 'Subaru'),
    ('PN1003', 2, 'KNDJN2A29F7345678', 2015, 'Kia'),
    ('PN1003', 3, '1FMCU0GX4DUA45678', 2013, 'Ford'),
    ('PN1004', 1, '1G1ZD5ST0JF567890', 2018, 'Chevrolet'),
    ('PN1005', 1, '1N4AL3AP8JC678901', 2018, 'Nissan'),
    ('PN1006', 1, 'JM1BM1V30E1789012', 2014, 'Mazda'),
    ('PN1006', 2, '2HKRM4H75EH890123', 2014, 'Honda');

create table raw.iw_account (acct_no varchar, insd_name varchar, street varchar, state varchar);
insert into raw.iw_account values
    ('50012     ', 'Ridgeline Freight LLC   ', '88 Quarry Rd      ', 'KY'),
    ('H7001     ', 'Bluegrass Plumbing Co   ', '210 Main St       ', 'TN'),
    ('H7002     ', 'J. Alvarez              ', '17 Birch Ave      ', 'KY'),
    ('H7003     ', 'Ozark Auto Sales        ', '3400 Route 66     ', 'MO');

create table raw.iw_policy (policy_no varchar, term_sfx varchar, acct_no varchar, state varchar, lob varchar,
                                  eff_yyyymmdd varchar, exp_yyyymmdd varchar, status varchar);
insert into raw.iw_policy values
    ('IW2001', '01', '50012     ', 'KY', 'CA', '20250401', '20260401', 'EX'),
    ('IW2001', '02', '50012     ', 'KY', 'CA', '20260401', '20270401', 'IF'),
    ('IW2002', '01', 'H7001     ', 'TN', 'CA', '20260115', '20270115', 'IF'),
    ('IW2003', '01', 'H7002     ', 'KY', 'PA', '20260601', '20261201', 'IF'),
    ('IW2004', '01', 'H7003     ', 'MO', 'CA', '20251101', '20261101', 'IF');

create table raw.iw_vehicle (policy_no varchar, unit_no integer, vin varchar, yr integer, make varchar);
insert into raw.iw_vehicle values
    ('IW2001', 1, '1XPBDP9X1FD234561', 2015, 'Peterbilt'),
    ('IW2001', 2, '1XKYD49X0GJ345672', 2016, 'Kenworth'),
    ('IW2002', 1, '1FTBF2A69HE456783', 2017, 'Ford'),
    ('IW2002', 2, '3C6TR5DT5KG567894', 2019, 'Ram'),
    ('IW2002', 3, 'JALC4B16XG7678905', 2016, 'Isuzu'),
    ('IW2003', 1, '5TFAZ5CN7KX789016', 2019, 'Toyota'),
    ('IW2004', 1, '1FDUF5HT1KEC89027', 2019, 'Ford');
"""

