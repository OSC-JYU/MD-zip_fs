IMAGES := $(shell docker images -f "dangling=true" -q)
CONTAINERS := $(shell docker ps -a -q -f status=exited)
VOLUME := md-zip_fs
VERSION := 0.1
REPOSITORY := local
IMAGE := md-zip_fs

ifneq (,$(wildcard .env))
    include .env
    export
endif

ifeq ($(MD_PATH),)
    $(error MD_PATH is not set. Please set it in .env file or environment)
endif



print-env:
	@echo "MD_PATH: $(MD_PATH)"

clean:
	docker rm -f $(CONTAINERS)
	docker rmi -f $(IMAGES)

build:
	docker build -t $(REPOSITORY)/messydesk/$(IMAGE):$(VERSION) .

start:
	docker run -d --replace --name $(IMAGE) \
		-p 9004:9004 \
		-e MD_URL=http://host.containers.internal:8200 \
		-v $(MD_PATH)/data/:/app/data:Z \
		--restart unless-stopped \
		$(REPOSITORY)/messydesk/$(IMAGE):$(VERSION)

restart:
	docker stop $(IMAGE)
	docker rm $(IMAGE)
	$(MAKE) start

bash:
	docker exec -it $(IMAGE) bash

