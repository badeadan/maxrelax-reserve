# MaxRelax reservations

Container to make automatic reservations on http://maxrelax.ro/

## Usage

Configure reservation parameters:

- crontab: when to make reservations. See https://linux.die.net/man/5/crontab
  manual page or https://crontab.guru/ (including examples section) on more info
  about setting the 5 fields that control the time and date.
  
- credentials: user and password used to login into MaxRelax appointments web
  page.
  
- reservations: dictionary of name to time slot reservations to make. Names need
  to match exactly the values already existing on the appointments page. This
  script does not create new accounts.

Example:

    crontab: '1 8 * * 1'
    credentials:
      user: 'user'
      password: 'password'
    reservations:
      John Doe: '12:00'
    
will run at 8:01 on every Monday, login as user/password and make a reservation
for John Doe at 12:00.

Time zone is set to Europe/Bucharest and is not configurable (MaxRelax.ro is
located in Bucharest). 

Start the reservation service in background:

    docker-compose up -d

